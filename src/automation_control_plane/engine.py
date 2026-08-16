"""Durable governed workflow engine.

The engine coordinates policy, immutable workflow definitions, SQLite state,
leases, approvals, budgets, retries, and recovery.  It never executes arbitrary
commands: workers can invoke only callables present in ``HandlerRegistry``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
from typing import Any, Callable, Mapping

from .handlers import HandlerContext, HandlerRegistry, HandlerResult, builtin_registry
from .models import (
    MAX_BUDGET_UNITS,
    MAX_DEADLINE_SECONDS,
    WorkflowDefinition,
    canonical_json,
    digest_json,
)
from .policy import capability_matches, require
from .storage import ConflictError, ControlPlaneStore, NotFoundError, utc_timestamp


MAX_IDEMPOTENCY_BYTES = 256
MAX_ERROR_BYTES = 1_024
MAX_LEASE_SECONDS = 3_600
TERMINAL_JOBS = {"completed", "failed", "cancelled"}
TERMINAL_STEPS = {"succeeded", "failed", "cancelled", "skipped"}


class ControlPlaneError(RuntimeError):
    pass


class LeaseLostError(ConflictError):
    pass


class KillSwitchError(ControlPlaneError):
    pass


class BudgetError(ControlPlaneError):
    pass


def _bounded_text(value: Any, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} must be a bounded nonempty string")
    return value


def _truncate_utf8(value: str, maximum: int) -> str:
    return value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")


def _expected_version(value: int | None, label: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{label} must be a positive integer")


class ControlPlane:
    def __init__(
        self,
        store: ControlPlaneStore,
        *,
        registry: HandlerRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.registry = registry or builtin_registry()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _require(connection: sqlite3.Connection, store: ControlPlaneStore, principal: str, capability: str) -> set[str]:
        _bounded_text(principal, "principal")
        capabilities = store.capabilities(principal, connection)
        require(principal, capabilities, capability)
        return capabilities

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(12)}"

    @staticmethod
    def _is_killed(connection: sqlite3.Connection, workflow_id: str) -> tuple[bool, str | None]:
        row = connection.execute(
            """SELECT scope, reason FROM kill_switches
               WHERE enabled = 1 AND ((scope = 'global' AND scope_id = '') OR
                                      (scope = 'workflow' AND scope_id = ?))
               ORDER BY CASE scope WHEN 'global' THEN 0 ELSE 1 END LIMIT 1""",
            (workflow_id,),
        ).fetchone()
        return (row is not None, row["reason"] if row else None)

    @staticmethod
    def _sync_pending_approvals(connection: sqlite3.Connection, job_id: str) -> None:
        row = connection.execute("SELECT version FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is not None:
            connection.execute(
                """UPDATE approvals SET job_version = ?
                   WHERE job_id = ? AND status IN ('pending', 'approved')""",
                (row["version"], job_id),
            )

    def _terminalize_job(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        state: str,
        now: str,
        reason: str,
        principal: str = "system",
        emit_event: bool = True,
    ) -> str:
        if state not in {"failed", "cancelled"}:
            raise ValueError("terminalization state must be failed or cancelled")
        job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError(f"job not found: {job_id}")
        if job["state"] == "completed":
            raise ConflictError("completed job cannot be terminalized")
        active = connection.execute(
            """SELECT step_id, state FROM step_runs
               WHERE job_id = ? AND state NOT IN ('succeeded', 'failed', 'cancelled', 'skipped')""",
            (job_id,),
        ).fetchall()
        connection.execute(
            """UPDATE step_runs SET state = 'cancelled', version = version + 1,
                   error = COALESCE(error, ?), reserved_cost = 0,
                   lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                   lease_fence_generation = NULL, updated_at = ?
               WHERE job_id = ? AND state NOT IN ('succeeded', 'failed', 'cancelled', 'skipped')""",
            (reason[:MAX_ERROR_BYTES], now, job_id),
        )
        connection.execute("UPDATE step_runs SET reserved_cost = 0 WHERE job_id = ?", (job_id,))
        if job["state"] not in TERMINAL_JOBS:
            connection.execute(
                """UPDATE jobs SET state = ?, version = version + 1, budget_reserved = 0,
                       fence_generation = fence_generation + 1, updated_at = ? WHERE job_id = ?""",
                (state, now, job_id),
            )
            if emit_event:
                self.store.append_event(
                    connection,
                    event_type=f"job.{state}",
                    entity_type="job",
                    entity_id=job_id,
                    principal=principal,
                    occurred_at=now,
                    payload={"state": state, "reason": reason, "fenced_steps": [row["step_id"] for row in active]},
                )
        elif active or job["budget_reserved"]:
            connection.execute(
                """UPDATE jobs SET version = version + 1, budget_reserved = 0,
                       fence_generation = fence_generation + 1, updated_at = ? WHERE job_id = ?""",
                (now, job_id),
            )
            if emit_event:
                self.store.append_event(
                    connection,
                    event_type="job.fence_repaired",
                    entity_type="job",
                    entity_id=job_id,
                    principal=principal,
                    occurred_at=now,
                    payload={"state": job["state"], "reason": reason, "fenced_steps": [row["step_id"] for row in active]},
                )
        return job["state"] if job["state"] in TERMINAL_JOBS else state

    def register_workflow(
        self, definition: WorkflowDefinition | Mapping[str, Any], *, principal: str, activate: bool = True
    ) -> dict[str, Any]:
        if not isinstance(activate, bool):
            raise ValueError("activate must be a boolean")
        workflow = definition if isinstance(definition, WorkflowDefinition) else WorkflowDefinition.from_dict(definition)
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, principal, "workflow:register")
            existing = connection.execute(
                "SELECT digest, active FROM workflows WHERE workflow_id = ? AND version = ?",
                (workflow.workflow_id, workflow.version),
            ).fetchone()
            if existing:
                if existing["digest"] != workflow.digest:
                    raise ConflictError("workflow version is immutable and already has different content")
                became_active = bool(activate and not existing["active"])
                if became_active:
                    connection.execute("UPDATE workflows SET active = 0 WHERE workflow_id = ?", (workflow.workflow_id,))
                    connection.execute(
                        "UPDATE workflows SET active = 1 WHERE workflow_id = ? AND version = ?",
                        (workflow.workflow_id, workflow.version),
                    )
                    self.store.append_event(
                        connection,
                        event_type="workflow.activated",
                        entity_type="workflow",
                        entity_id=f"{workflow.workflow_id}:{workflow.version}",
                        principal=principal,
                        occurred_at=now,
                        payload={"digest": workflow.digest},
                    )
                return {
                    "workflow_id": workflow.workflow_id,
                    "version": workflow.version,
                    "digest": workflow.digest,
                    "active": bool(existing["active"] or became_active),
                    "created": False,
                }
            if activate:
                connection.execute("UPDATE workflows SET active = 0 WHERE workflow_id = ?", (workflow.workflow_id,))
            connection.execute(
                """INSERT INTO workflows(workflow_id, version, definition_json, digest, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    workflow.workflow_id,
                    workflow.version,
                    canonical_json(workflow.to_dict()),
                    workflow.digest,
                    int(activate),
                    now,
                ),
            )
            self.store.append_event(
                connection,
                event_type="workflow.registered",
                entity_type="workflow",
                entity_id=f"{workflow.workflow_id}:{workflow.version}",
                principal=principal,
                occurred_at=now,
                payload={"active": activate, "digest": workflow.digest},
            )
        return {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "digest": workflow.digest,
            "active": activate,
            "created": True,
        }

    def assign_role(self, target_principal: str, role: str, *, principal: str) -> dict[str, str]:
        _bounded_text(target_principal, "target principal")
        _bounded_text(role, "role")
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, principal, "role:assign")
            if connection.execute("SELECT 1 FROM roles WHERE role_name = ?", (role,)).fetchone() is None:
                raise NotFoundError(f"role not found: {role}")
            connection.execute(
                "INSERT OR IGNORE INTO principal_roles(principal, role_name) VALUES (?, ?)",
                (target_principal, role),
            )
            self.store.append_event(
                connection,
                event_type="role.assigned",
                entity_type="principal",
                entity_id=target_principal,
                principal=principal,
                occurred_at=now,
                payload={"role": role},
            )
        return {"principal": target_principal, "role": role}

    def submit(
        self,
        workflow_id: str,
        *,
        principal: str,
        trigger: Mapping[str, Any],
        idempotency_key: str,
        payload: Mapping[str, Any] | None = None,
        workflow_version: int | None = None,
        budget_units: int | None = None,
        deadline_seconds: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        _bounded_text(workflow_id, "workflow id", maximum=128)
        _bounded_text(idempotency_key, "idempotency key", maximum=MAX_IDEMPOTENCY_BYTES)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError("payload must be an object")
        if not isinstance(trigger, Mapping):
            raise ValueError("trigger must be an object")
        payload_value = dict(payload or {})
        payload_json = canonical_json(payload_value)
        trigger_json = canonical_json(dict(trigger))
        with self.store.transaction() as connection:
            now_value = self._now()
            now = utc_timestamp(now_value)
            capabilities = self._require(connection, self.store, principal, "job:submit")
            workflow = self.store.load_workflow(workflow_id, workflow_version, connection)
            supplied_trigger = json.loads(trigger_json)
            if not workflow.accepts_trigger(supplied_trigger):
                raise ValueError("trigger is not declared by the workflow version")
            require(principal, capabilities, f"trigger:{supplied_trigger['type']}")
            killed, reason = self._is_killed(connection, workflow.workflow_id)
            if killed:
                raise KillSwitchError(f"submission blocked by kill switch: {reason}")
            budget = workflow.budget_units if budget_units is None else budget_units
            if isinstance(budget, bool) or not isinstance(budget, int) or not 0 <= budget <= workflow.budget_units:
                raise ValueError("budget_units must be an integer within the workflow budget")
            deadline_delta = workflow.default_deadline_seconds if deadline_seconds is None else deadline_seconds
            if (
                isinstance(deadline_delta, bool)
                or not isinstance(deadline_delta, int)
                or not 1 <= deadline_delta <= min(workflow.default_deadline_seconds, MAX_DEADLINE_SECONDS)
            ):
                raise ValueError("deadline_seconds must be positive and no greater than the workflow default")
            request_digest = digest_json(
                {
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.version,
                    "trigger": supplied_trigger,
                    "payload": payload_value,
                    "budget_units": budget,
                    "deadline_seconds": deadline_delta,
                    "dry_run": dry_run,
                    "submitted_by": principal,
                }
            )
            existing = connection.execute(
                "SELECT job_id, request_digest FROM jobs WHERE workflow_id = ? AND idempotency_key = ?",
                (workflow.workflow_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_digest"] != request_digest:
                    raise ConflictError("idempotency key was already used for a different submission")
                result = self.store.get_job(existing["job_id"])
                result["idempotent_replay"] = True
                return result
            deadline = utc_timestamp(now_value + timedelta(seconds=deadline_delta))
            job_id = self._new_id("job")
            root_states = []
            for step in workflow.steps:
                if step.depends_on:
                    root_states.append("blocked")
                elif step.approval == "required":
                    root_states.append("waiting_approval")
                else:
                    root_states.append("ready")
            if "ready" in root_states:
                job_state = "queued"
            elif "waiting_approval" in root_states:
                job_state = "waiting_approval"
            else:
                job_state = "queued"
            connection.execute(
                """INSERT INTO jobs(
                       job_id, workflow_id, workflow_version, workflow_digest, state, version,
                       trigger_type, trigger_json, payload_json, idempotency_key, request_digest, submitted_by,
                       budget_limit, budget_spent, budget_reserved, fence_generation,
                       deadline_at, dry_run, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)""",
                (
                    job_id,
                    workflow.workflow_id,
                    workflow.version,
                    workflow.digest,
                    job_state,
                    supplied_trigger["type"],
                    trigger_json,
                    payload_json,
                    idempotency_key,
                    request_digest,
                    principal,
                    budget,
                    deadline,
                    int(dry_run),
                    now,
                    now,
                ),
            )
            for step, state in zip(workflow.steps, root_states, strict=True):
                connection.execute(
                    """INSERT INTO step_runs(
                           job_id, step_id, state, version, attempts, max_attempts, available_at,
                           approval_required, input_digest, estimated_cost, reserved_cost, charged_cost,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        job_id,
                        step.id,
                        state,
                        step.retry.max_attempts,
                        now,
                        int(step.approval == "required"),
                        digest_json(dict(step.input)),
                        step.estimated_cost,
                        now,
                        now,
                    ),
                )
                if state == "waiting_approval":
                    self._request_approval(connection, job_id, step.id, workflow.digest, 1, 1, digest_json(dict(step.input)), now)
            self.store.append_event(
                connection,
                event_type="job.submitted",
                entity_type="job",
                entity_id=job_id,
                principal=principal,
                occurred_at=now,
                payload={
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.version,
                    "workflow_digest": workflow.digest,
                    "trigger_type": supplied_trigger["type"],
                    "budget_limit": budget,
                    "deadline_at": deadline,
                    "dry_run": dry_run,
                },
            )
            waiting = [step.id for step, state in zip(workflow.steps, root_states, strict=True) if state == "waiting_approval"]
            for step_id in waiting:
                self.store.append_event(
                    connection,
                    event_type="approval.requested",
                    entity_type="step",
                    entity_id=f"{job_id}:{step_id}",
                    principal="system",
                    occurred_at=now,
                    payload={"job_id": job_id, "step_id": step_id, "workflow_digest": workflow.digest},
                )
        result = self.store.get_job(job_id)
        result["idempotent_replay"] = False
        return result

    def _request_approval(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        step_id: str,
        workflow_digest: str,
        job_version: int,
        step_version: int,
        input_digest: str,
        now: str,
    ) -> None:
        connection.execute(
            """INSERT INTO approvals(
                   approval_id, job_id, step_id, workflow_digest, job_version, step_version,
                   input_digest, status, requested_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                self._new_id("approval"),
                job_id,
                step_id,
                workflow_digest,
                job_version,
                step_version,
                input_digest,
                now,
            ),
        )

    def decide_approval(
        self,
        job_id: str,
        step_id: str,
        *,
        principal: str,
        decision: str,
        reason: str,
        expected_job_version: int | None = None,
        expected_step_version: int | None = None,
    ) -> dict[str, Any]:
        _bounded_text(job_id, "job id", maximum=256)
        _bounded_text(step_id, "step id", maximum=128)
        _expected_version(expected_job_version, "expected_job_version")
        _expected_version(expected_step_version, "expected_step_version")
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        _bounded_text(reason, "approval reason", maximum=1_024)
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            capabilities = self._require(connection, self.store, principal, "approval:decide")
            job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            step = connection.execute(
                "SELECT * FROM step_runs WHERE job_id = ? AND step_id = ?", (job_id, step_id)
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM approvals WHERE job_id = ? AND step_id = ?", (job_id, step_id)
            ).fetchone()
            if job is None or step is None or approval is None:
                raise NotFoundError("job step or approval not found")
            if expected_job_version is not None and job["version"] != expected_job_version:
                raise ConflictError("job version conflict")
            if expected_step_version is not None and step["version"] != expected_step_version:
                raise ConflictError("step version conflict")
            if job["state"] in TERMINAL_JOBS or step["state"] != "waiting_approval" or approval["status"] != "pending":
                raise ConflictError("approval is no longer pending")
            if principal == job["submitted_by"] and not any(
                capability_matches(item, "approval:self") for item in capabilities
            ):
                raise PermissionError("submitter cannot approve their own job")
            if (
                approval["workflow_digest"] != job["workflow_digest"]
                or approval["input_digest"] != step["input_digest"]
                or approval["step_version"] != step["version"]
                or approval["job_version"] != job["version"]
            ):
                raise ConflictError("approval binding no longer matches step")
            next_step_version = step["version"] + 1
            connection.execute(
                """UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, reason = ?,
                       job_version = ?, step_version = ? WHERE approval_id = ?""",
                (decision, principal, now, reason, job["version"], next_step_version, approval["approval_id"]),
            )
            if decision == "approved":
                connection.execute(
                    """UPDATE step_runs SET state = 'ready', version = ?, available_at = ?, updated_at = ?
                       WHERE job_id = ? AND step_id = ?""",
                    (next_step_version, now, now, job_id, step_id),
                )
            else:
                connection.execute(
                    """UPDATE step_runs SET state = 'failed', version = ?, error = 'approval_rejected', updated_at = ?
                       WHERE job_id = ? AND step_id = ?""",
                    (next_step_version, now, job_id, step_id),
                )
            connection.execute(
                "UPDATE jobs SET version = version + 1, updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            self._sync_pending_approvals(connection, job_id)
            if decision == "rejected":
                workflow = self.store.load_workflow(job["workflow_id"], job["workflow_version"], connection)
                self._promote_steps(connection, job, workflow, now)
            self._refresh_job_state(connection, job_id, now)
            current_job_version = connection.execute(
                "SELECT version FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()["version"]
            connection.execute(
                "UPDATE approvals SET job_version = ? WHERE approval_id = ?",
                (current_job_version, approval["approval_id"]),
            )
            self.store.append_event(
                connection,
                event_type=f"approval.{decision}",
                entity_type="step",
                entity_id=f"{job_id}:{step_id}",
                principal=principal,
                occurred_at=now,
                payload={
                    "approval_id": approval["approval_id"],
                    "workflow_digest": job["workflow_digest"],
                    "input_digest": step["input_digest"],
                    "reason": reason,
                },
            )
        return self.store.get_job(job_id)

    def set_kill_switch(
        self,
        *,
        scope: str,
        scope_id: str,
        enabled: bool,
        reason: str,
        principal: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        _expected_version(expected_version, "expected_version")
        if scope not in {"global", "workflow"}:
            raise ValueError("scope must be global or workflow")
        if (scope == "global" and scope_id != "") or (scope == "workflow" and not scope_id):
            raise ValueError("scope_id must be empty for global and nonempty for workflow")
        if scope == "workflow":
            _bounded_text(scope_id, "scope_id", maximum=128)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        _bounded_text(reason, "kill switch reason", maximum=1_024)
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, principal, "kill:switch")
            if scope == "workflow" and connection.execute(
                "SELECT 1 FROM workflows WHERE workflow_id = ? LIMIT 1", (scope_id,)
            ).fetchone() is None:
                raise NotFoundError(f"workflow not found: {scope_id}")
            current = connection.execute(
                "SELECT * FROM kill_switches WHERE scope = ? AND scope_id = ?", (scope, scope_id)
            ).fetchone()
            if expected_version is not None and (current is None or current["version"] != expected_version):
                raise ConflictError("kill switch version conflict")
            version = 1 if current is None else current["version"] + 1
            connection.execute(
                """INSERT INTO kill_switches(scope, scope_id, enabled, reason, version, updated_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope, scope_id) DO UPDATE SET
                     enabled = excluded.enabled, reason = excluded.reason, version = excluded.version,
                     updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                (scope, scope_id, int(enabled), reason, version, principal, now),
            )
            fenced_jobs: list[str] = []
            if enabled:
                if scope == "global":
                    rows = connection.execute(
                        "SELECT job_id FROM jobs WHERE state NOT IN ('completed', 'failed', 'cancelled')"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """SELECT job_id FROM jobs WHERE workflow_id = ?
                           AND state NOT IN ('completed', 'failed', 'cancelled')""",
                        (scope_id,),
                    ).fetchall()
                for row in rows:
                    self._terminalize_job(
                        connection,
                        row["job_id"],
                        state="cancelled",
                        now=now,
                        reason=f"kill_switch:{scope}:{scope_id}",
                        principal=principal,
                    )
                    fenced_jobs.append(row["job_id"])
            self.store.append_event(
                connection,
                event_type="kill_switch.enabled" if enabled else "kill_switch.disabled",
                entity_type="kill_switch",
                entity_id=f"{scope}:{scope_id}",
                principal=principal,
                occurred_at=now,
                payload={
                    "scope": scope,
                    "scope_id": scope_id,
                    "enabled": enabled,
                    "reason": reason,
                    "version": version,
                    "fenced_jobs": fenced_jobs,
                },
            )
        return {"scope": scope, "scope_id": scope_id, "enabled": enabled, "reason": reason, "version": version}

    def cancel_job(
        self, job_id: str, *, principal: str, reason: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        _bounded_text(job_id, "job id", maximum=256)
        _expected_version(expected_version, "expected_version")
        _bounded_text(reason, "cancellation reason", maximum=1_024)
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, principal, "job:cancel")
            job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                raise NotFoundError(f"job not found: {job_id}")
            if expected_version is not None and job["version"] != expected_version:
                raise ConflictError("job version conflict")
            if job["state"] in TERMINAL_JOBS:
                if job["state"] == "cancelled":
                    return self.store.get_job(job_id)
                raise ConflictError("terminal job cannot be cancelled")
            self._terminalize_job(
                connection,
                job_id,
                state="cancelled",
                now=now,
                reason=f"cancelled:{reason}",
                principal=principal,
                emit_event=False,
            )
            self.store.append_event(
                connection,
                event_type="job.cancelled",
                entity_type="job",
                entity_id=job_id,
                principal=principal,
                occurred_at=now,
                payload={"reason": reason},
            )
        return self.store.get_job(job_id)

    def claim_step(self, *, worker: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("lease_seconds is out of bounds")
        with self.store.transaction() as connection:
            now_value = self._now()
            now = utc_timestamp(now_value)
            capabilities = self._require(connection, self.store, worker, "job:claim")
            self._recover_expired_in_tx(connection, now_value, worker)
            global_kill, _ = self._is_killed(connection, "__none__")
            if global_kill:
                return None
            candidates = connection.execute(
                """SELECT sr.*, j.workflow_id, j.workflow_version, j.workflow_digest,
                          j.deadline_at, j.dry_run, j.budget_limit, j.budget_spent,
                          j.budget_reserved, j.fence_generation, j.version AS job_version
                   FROM step_runs sr JOIN jobs j ON j.job_id = sr.job_id
                   WHERE sr.state = 'ready' AND sr.available_at <= ?
                     AND j.state NOT IN ('completed', 'failed', 'cancelled') AND j.deadline_at > ?
                   ORDER BY sr.available_at, j.created_at, sr.rowid""",
                (now, now),
            )
            selected = None
            selected_definition = None
            registered_handlers = set(self.registry.names())
            workflow_cache: dict[tuple[str, int], WorkflowDefinition] = {}
            for candidate in candidates:
                current_job = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (candidate["job_id"],)
                ).fetchone()
                current_step = connection.execute(
                    "SELECT * FROM step_runs WHERE job_id = ? AND step_id = ?",
                    (candidate["job_id"], candidate["step_id"]),
                ).fetchone()
                if (
                    current_job is None
                    or current_step is None
                    or current_job["state"] in TERMINAL_JOBS
                    or current_job["deadline_at"] <= now
                    or current_step["state"] != "ready"
                    or current_step["available_at"] > now
                ):
                    continue
                killed, _ = self._is_killed(connection, candidate["workflow_id"])
                if killed:
                    continue
                workflow_key = (candidate["workflow_id"], candidate["workflow_version"])
                workflow = workflow_cache.get(workflow_key)
                if workflow is None:
                    workflow = self.store.load_workflow(*workflow_key, connection=connection)
                    workflow_cache[workflow_key] = workflow
                definition = workflow.step(candidate["step_id"])
                if definition.handler not in registered_handlers:
                    continue
                trusted_capability = self.registry.required_capability(definition.handler)
                if not any(capability_matches(item, trusted_capability) for item in capabilities):
                    continue
                if not any(capability_matches(item, definition.required_capability) for item in capabilities):
                    continue
                if current_job["budget_spent"] + current_step["estimated_cost"] > current_job["budget_limit"]:
                    connection.execute(
                        """UPDATE step_runs SET state = 'failed', version = version + 1,
                               error = 'budget_exhausted', reserved_cost = 0, updated_at = ?
                           WHERE job_id = ? AND step_id = ? AND state = 'ready'""",
                        (now, candidate["job_id"], candidate["step_id"]),
                    )
                    self._promote_steps(connection, candidate, workflow, now)
                    self._refresh_job_state(connection, candidate["job_id"], now)
                    self.store.append_event(
                        connection,
                        event_type="step.failed",
                        entity_type="step",
                        entity_id=f"{candidate['job_id']}:{candidate['step_id']}",
                        principal="system",
                        occurred_at=now,
                        payload={"reason": "budget_exhausted"},
                    )
                    continue
                if (
                    current_job["budget_spent"]
                    + current_job["budget_reserved"]
                    + current_step["estimated_cost"]
                    > current_job["budget_limit"]
                ):
                    # Another active lease owns this capacity.  Skipping is
                    # essential: failing here would fence work that is still
                    # legitimately running and could settle below estimate.
                    continue
                selected = dict(candidate)
                selected.update(dict(current_job))
                selected.update(dict(current_step))
                selected["job_version"] = current_job["version"]
                selected_definition = definition
                break
            if selected is None or selected_definition is None:
                return None
            if selected["approval_required"]:
                approval = connection.execute(
                    "SELECT * FROM approvals WHERE job_id = ? AND step_id = ? AND status = 'approved'",
                    (selected["job_id"], selected["step_id"]),
                ).fetchone()
                if (
                    approval is None
                    or approval["workflow_digest"] != selected["workflow_digest"]
                    or approval["input_digest"] != selected["input_digest"]
                    or approval["step_version"] != selected["version"]
                    or approval["job_version"] != selected["job_version"]
                ):
                    raise ConflictError("approved step lacks a current bound approval")
            lease_proof = secrets.token_hex(24)
            lease_duration = min(lease_seconds, selected_definition.timeout_seconds)
            lease_expires = utc_timestamp(now_value + timedelta(seconds=lease_duration))
            reserved = selected["estimated_cost"]
            job_updated = connection.execute(
                """UPDATE jobs SET budget_reserved = budget_reserved + ?, state = 'running',
                       version = version + 1, updated_at = ?
                   WHERE job_id = ? AND state NOT IN ('completed', 'failed', 'cancelled')
                     AND fence_generation = ?
                     AND budget_spent + budget_reserved + ? <= budget_limit""",
                (reserved, now, selected["job_id"], selected["fence_generation"], reserved),
            )
            if job_updated.rowcount != 1:
                raise ConflictError("job changed or budget was exhausted while claiming")
            current_job = connection.execute(
                "SELECT version, fence_generation FROM jobs WHERE job_id = ?", (selected["job_id"],)
            ).fetchone()
            updated = connection.execute(
                """UPDATE step_runs SET state = 'leased', version = version + 1,
                       attempts = attempts + 1, lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                       lease_fence_generation = ?, reserved_cost = ?, updated_at = ?
                   WHERE job_id = ? AND step_id = ? AND state = 'ready' AND version = ?""",
                (
                    worker,
                    lease_proof,
                    lease_expires,
                    current_job["fence_generation"],
                    reserved,
                    now,
                    selected["job_id"],
                    selected["step_id"],
                    selected["version"],
                ),
            )
            if updated.rowcount != 1:
                raise ConflictError("step changed while claiming")
            self._sync_pending_approvals(connection, selected["job_id"])
            if selected["approval_required"]:
                connection.execute(
                    """UPDATE approvals SET job_version = ?, step_version = ?
                       WHERE job_id = ? AND step_id = ? AND status = 'approved'""",
                    (
                        current_job["version"],
                        selected["version"] + 1,
                        selected["job_id"],
                        selected["step_id"],
                    ),
                )
            self.store.append_event(
                connection,
                event_type="step.leased",
                entity_type="step",
                entity_id=f"{selected['job_id']}:{selected['step_id']}",
                principal=worker,
                occurred_at=now,
                payload={"attempt": selected["attempts"] + 1, "lease_expires_at": lease_expires},
            )
            return {
                "job_id": selected["job_id"],
                "workflow_id": selected["workflow_id"],
                "workflow_version": selected["workflow_version"],
                "step_id": selected["step_id"],
                "handler": selected_definition.handler,
                "input": json.loads(canonical_json(selected_definition.input)),
                "attempt": selected["attempts"] + 1,
                "lease_token": lease_proof,
                "lease_expires_at": lease_expires,
                "deadline_at": selected["deadline_at"],
                "dry_run": bool(selected["dry_run"]),
                "estimated_cost": selected["estimated_cost"],
                "fence_generation": current_job["fence_generation"],
            }

    def execute_once(self, *, worker: str, lease_seconds: int = 60) -> dict[str, Any]:
        lease = self.claim_step(worker=worker, lease_seconds=lease_seconds)
        if lease is None:
            return {"status": "idle", "worker": worker}
        if lease["dry_run"]:
            result = HandlerResult(
                {
                    "status": "dry_run",
                    "handler": lease["handler"],
                    "input_digest": digest_json(lease["input"]),
                    "estimated_cost": lease["estimated_cost"],
                },
                cost_units=0,
            )
            return self.complete_step(lease, worker=worker, result=result)
        context = HandlerContext(
            job_id=lease["job_id"],
            workflow_id=lease["workflow_id"],
            workflow_version=lease["workflow_version"],
            step_id=lease["step_id"],
            attempt=lease["attempt"],
            deadline_at=lease["deadline_at"],
            dry_run=False,
            input=lease["input"],
        )
        try:
            result = self.registry.execute(lease["handler"], context)
        except Exception as exc:
            message = _truncate_utf8(
                f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')}",
                MAX_ERROR_BYTES,
            )
            return self.fail_step(lease, worker=worker, error=message)
        return self.complete_step(lease, worker=worker, result=result)

    def complete_step(
        self, lease: Mapping[str, Any], *, worker: str, result: HandlerResult
    ) -> dict[str, Any]:
        if not isinstance(result, HandlerResult):
            raise TypeError("result must be HandlerResult")
        output_json = canonical_json(dict(result.output))
        output_digest = digest_json(result.output)
        with self.store.transaction() as connection:
            now_value = self._now()
            now = utc_timestamp(now_value)
            self._require(connection, self.store, worker, "job:claim")
            step, job, workflow, definition = self._leased_context(connection, lease, worker, now)
            killed, kill_reason = self._is_killed(connection, job["workflow_id"])
            if killed:
                self._cancel_leased_step(connection, step, job, now, f"kill_switch:{kill_reason}")
                self.store.append_event(
                    connection,
                    event_type="step.cancelled",
                    entity_type="step",
                    entity_id=f"{job['job_id']}:{step['step_id']}",
                    principal="system",
                    occurred_at=now,
                    payload={"reason": "kill_switch"},
                )
                return {"status": "cancelled", "job_id": job["job_id"], "step_id": step["step_id"]}
            if now >= job["deadline_at"] or now >= step["lease_expires_at"]:
                raise LeaseLostError("lease or job deadline expired before completion")
            other_reserved = job["budget_reserved"] - step["reserved_cost"]
            if (
                other_reserved < 0
                or result.cost_units > MAX_BUDGET_UNITS
                or job["budget_spent"] + other_reserved + result.cost_units > job["budget_limit"]
            ):
                return self._fail_step_in_tx(
                    connection, step, job, workflow, definition, now_value, worker, "budget_exhausted"
                )
            updated = connection.execute(
                """UPDATE step_runs SET state = 'succeeded', version = version + 1,
                       result_json = ?, result_digest = ?, charged_cost = ?, reserved_cost = 0, error = NULL,
                       lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                       lease_fence_generation = NULL, updated_at = ?
                   WHERE job_id = ? AND step_id = ? AND state = 'leased' AND lease_token = ?""",
                (
                    output_json,
                    output_digest,
                    result.cost_units,
                    now,
                    job["job_id"],
                    step["step_id"],
                    lease["lease_token"],
                ),
            )
            if updated.rowcount != 1:
                raise LeaseLostError("lease was lost during completion")
            connection.execute(
                """UPDATE jobs SET budget_spent = budget_spent + ?,
                       budget_reserved = budget_reserved - ?, version = version + 1, updated_at = ?
                   WHERE job_id = ?""",
                (result.cost_units, step["reserved_cost"], now, job["job_id"]),
            )
            self._sync_pending_approvals(connection, job["job_id"])
            self.store.append_event(
                connection,
                event_type="step.succeeded",
                entity_type="step",
                entity_id=f"{job['job_id']}:{step['step_id']}",
                principal=worker,
                occurred_at=now,
                payload={"attempt": step["attempts"], "cost_units": result.cost_units, "output_digest": output_digest},
            )
            refreshed_job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job["job_id"],)).fetchone()
            self._promote_steps(connection, refreshed_job, workflow, now)
            self._refresh_job_state(connection, job["job_id"], now)
        return {"status": "succeeded", "job_id": job["job_id"], "step_id": step["step_id"], "cost_units": result.cost_units}

    def fail_step(self, lease: Mapping[str, Any], *, worker: str, error: str) -> dict[str, Any]:
        _bounded_text(error, "error", maximum=MAX_ERROR_BYTES)
        with self.store.transaction() as connection:
            now_value = self._now()
            now = utc_timestamp(now_value)
            self._require(connection, self.store, worker, "job:claim")
            step, job, workflow, definition = self._leased_context(connection, lease, worker, now, allow_expired=False)
            return self._fail_step_in_tx(connection, step, job, workflow, definition, now_value, worker, error)

    def _leased_context(
        self,
        connection: sqlite3.Connection,
        lease: Mapping[str, Any],
        worker: str,
        now: str,
        *,
        allow_expired: bool = False,
    ) -> tuple[sqlite3.Row, sqlite3.Row, WorkflowDefinition, Any]:
        required = {"job_id", "step_id", "lease_token"}
        if not isinstance(lease, Mapping) or not required <= set(lease):
            raise ValueError("lease is missing required fields")
        step = connection.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND step_id = ?", (lease["job_id"], lease["step_id"])
        ).fetchone()
        job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (lease["job_id"],)).fetchone()
        if step is None or job is None:
            raise NotFoundError("leased job or step not found")
        if (
            job["state"] in TERMINAL_JOBS
            or step["state"] != "leased"
            or step["lease_owner"] != worker
            or not secrets.compare_digest(step["lease_token"] or "", str(lease["lease_token"]))
            or step["lease_fence_generation"] != job["fence_generation"]
        ):
            raise LeaseLostError("lease token or owner no longer matches")
        if not allow_expired and step["lease_expires_at"] <= now:
            raise LeaseLostError("lease has expired")
        workflow = self.store.load_workflow(job["workflow_id"], job["workflow_version"], connection)
        return step, job, workflow, workflow.step(step["step_id"])

    def _fail_step_in_tx(
        self,
        connection: sqlite3.Connection,
        step: sqlite3.Row,
        job: sqlite3.Row,
        workflow: WorkflowDefinition,
        definition: Any,
        now_value: datetime,
        principal: str,
        error: str,
    ) -> dict[str, Any]:
        now = utc_timestamp(now_value)
        killed, _ = self._is_killed(connection, job["workflow_id"])
        retry_allowed = (
            not killed
            and error != "budget_exhausted"
            and step["attempts"] < step["max_attempts"]
            and now < job["deadline_at"]
        )
        if retry_allowed:
            delay = definition.retry.delay_after_failure(step["attempts"])
            available = utc_timestamp(now_value + timedelta(seconds=delay))
            next_step_version = step["version"] + 1
            connection.execute(
                """UPDATE step_runs SET state = 'ready', version = version + 1, available_at = ?, error = ?,
                       reserved_cost = 0, lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, lease_fence_generation = NULL, updated_at = ?
                   WHERE job_id = ? AND step_id = ? AND state = 'leased'""",
                (available, error[:MAX_ERROR_BYTES], now, job["job_id"], step["step_id"]),
            )
            if step["approval_required"]:
                connection.execute(
                    """UPDATE approvals SET step_version = ?
                       WHERE job_id = ? AND step_id = ? AND status = 'approved'""",
                    (next_step_version, job["job_id"], step["step_id"]),
                )
            connection.execute(
                """UPDATE jobs SET budget_reserved = budget_reserved - ?,
                       version = version + 1, updated_at = ? WHERE job_id = ?""",
                (step["reserved_cost"], now, job["job_id"]),
            )
            self._sync_pending_approvals(connection, job["job_id"])
            self._refresh_job_state(connection, job["job_id"], now)
            if step["approval_required"]:
                current_job_version = connection.execute(
                    "SELECT version FROM jobs WHERE job_id = ?", (job["job_id"],)
                ).fetchone()["version"]
                connection.execute(
                    """UPDATE approvals SET job_version = ?
                       WHERE job_id = ? AND step_id = ? AND status = 'approved'""",
                    (current_job_version, job["job_id"], step["step_id"]),
                )
            event_type = "step.retry_scheduled"
            payload = {"attempt": step["attempts"], "available_at": available, "error": error[:MAX_ERROR_BYTES]}
            status = "retry_scheduled"
        else:
            final_state = "cancelled" if killed else "failed"
            connection.execute(
                """UPDATE step_runs SET state = ?, version = version + 1, error = ?,
                       reserved_cost = 0, lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, lease_fence_generation = NULL, updated_at = ?
                   WHERE job_id = ? AND step_id = ? AND state = 'leased'""",
                (final_state, error[:MAX_ERROR_BYTES], now, job["job_id"], step["step_id"]),
            )
            connection.execute(
                """UPDATE jobs SET budget_reserved = budget_reserved - ?,
                       version = version + 1, updated_at = ? WHERE job_id = ?""",
                (step["reserved_cost"], now, job["job_id"]),
            )
            self._sync_pending_approvals(connection, job["job_id"])
            self._promote_steps(connection, job, workflow, now)
            self._refresh_job_state(connection, job["job_id"], now)
            event_type = f"step.{final_state}"
            payload = {"attempt": step["attempts"], "error": error[:MAX_ERROR_BYTES]}
            status = final_state
        self.store.append_event(
            connection,
            event_type=event_type,
            entity_type="step",
            entity_id=f"{job['job_id']}:{step['step_id']}",
            principal=principal,
            occurred_at=now,
            payload=payload,
        )
        return {"status": status, "job_id": job["job_id"], "step_id": step["step_id"]}

    def _cancel_leased_step(
        self, connection: sqlite3.Connection, step: sqlite3.Row, job: sqlite3.Row, now: str, error: str
    ) -> None:
        self._terminalize_job(
            connection,
            job["job_id"],
            state="cancelled",
            now=now,
            reason=error,
            emit_event=False,
        )

    def _promote_steps(
        self, connection: sqlite3.Connection, job: Mapping[str, Any], workflow: WorkflowDefinition, now: str
    ) -> None:
        changed = True
        while changed:
            changed = False
            states = {
                row["step_id"]: row
                for row in connection.execute("SELECT * FROM step_runs WHERE job_id = ?", (job["job_id"],)).fetchall()
            }
            for definition in workflow.steps:
                current = states[definition.id]
                if current["state"] != "blocked":
                    continue
                dependency_states = [states[dependency]["state"] for dependency in definition.depends_on]
                if any(state in {"failed", "cancelled", "skipped"} for state in dependency_states):
                    connection.execute(
                        """UPDATE step_runs SET state = 'skipped', version = version + 1,
                               error = 'dependency_failed', updated_at = ?
                           WHERE job_id = ? AND step_id = ? AND state = 'blocked'""",
                        (now, job["job_id"], definition.id),
                    )
                    changed = True
                elif dependency_states and all(state == "succeeded" for state in dependency_states):
                    target = "waiting_approval" if definition.approval == "required" else "ready"
                    next_version = current["version"] + 1
                    connection.execute(
                        """UPDATE step_runs SET state = ?, version = ?, available_at = ?, updated_at = ?
                           WHERE job_id = ? AND step_id = ? AND state = 'blocked'""",
                        (target, next_version, now, now, job["job_id"], definition.id),
                    )
                    if target == "waiting_approval":
                        current_job = connection.execute(
                            "SELECT version, workflow_digest FROM jobs WHERE job_id = ?", (job["job_id"],)
                        ).fetchone()
                        self._request_approval(
                            connection,
                            job["job_id"],
                            definition.id,
                            current_job["workflow_digest"],
                            current_job["version"],
                            next_version,
                            current["input_digest"],
                            now,
                        )
                        self.store.append_event(
                            connection,
                            event_type="approval.requested",
                            entity_type="step",
                            entity_id=f"{job['job_id']}:{definition.id}",
                            principal="system",
                            occurred_at=now,
                            payload={"job_id": job["job_id"], "step_id": definition.id, "workflow_digest": current_job["workflow_digest"]},
                        )
                    changed = True

    def _refresh_job_state(self, connection: sqlite3.Connection, job_id: str, now: str) -> str:
        job = connection.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError(f"job not found: {job_id}")
        if job["state"] in TERMINAL_JOBS:
            if job["state"] in {"failed", "cancelled"}:
                self._terminalize_job(
                    connection,
                    job_id,
                    state=job["state"],
                    now=now,
                    reason="terminal_job_fence_repair",
                )
            return job["state"]
        states = [row["state"] for row in connection.execute(
            "SELECT state FROM step_runs WHERE job_id = ?", (job_id,)
        ).fetchall()]
        if states and all(state == "succeeded" for state in states):
            target = "completed"
        elif any(state == "failed" for state in states):
            return self._terminalize_job(
                connection, job_id, state="failed", now=now, reason="step_failed"
            )
        elif any(state == "leased" for state in states):
            target = "running"
        elif any(state == "ready" for state in states):
            target = "queued"
        elif any(state == "waiting_approval" for state in states):
            target = "waiting_approval"
        elif any(state == "blocked" for state in states):
            target = "queued"
        elif states and all(state in {"succeeded", "cancelled", "skipped"} for state in states):
            return self._terminalize_job(
                connection, job_id, state="cancelled", now=now, reason="no_remaining_work"
            )
        else:
            target = "failed"
        if target != job["state"]:
            connection.execute(
                "UPDATE jobs SET state = ?, version = version + 1, updated_at = ? WHERE job_id = ?",
                (target, now, job_id),
            )
            self._sync_pending_approvals(connection, job_id)
            self.store.append_event(
                connection,
                event_type=f"job.{target}",
                entity_type="job",
                entity_id=job_id,
                principal="system",
                occurred_at=now,
                payload={"state": target},
            )
        return target

    def recover(self, *, principal: str = "admin") -> dict[str, int]:
        with self.store.transaction() as connection:
            now_value = self._now()
            self._require(connection, self.store, principal, "job:claim")
            return self._recover_expired_in_tx(connection, now_value, principal)

    def _recover_expired_in_tx(
        self, connection: sqlite3.Connection, now_value: datetime, principal: str
    ) -> dict[str, int]:
        now = utc_timestamp(now_value)
        expired = connection.execute(
            """SELECT sr.*, j.workflow_id, j.workflow_version, j.workflow_digest,
                      j.deadline_at, j.state AS job_state
               FROM step_runs sr JOIN jobs j ON j.job_id = sr.job_id
               WHERE sr.state = 'leased' AND sr.lease_expires_at <= ?
                 AND j.state NOT IN ('completed', 'failed', 'cancelled')""",
            (now,),
        ).fetchall()
        retried = 0
        failed = 0
        for expired_step in expired:
            # A prior item from the same snapshot may have terminalized and
            # fenced the whole job. Re-read before every recovery decision so
            # stale snapshot rows cannot release a reservation twice or
            # resurrect a lease cancelled earlier in this transaction.
            step = connection.execute(
                """SELECT sr.*, j.workflow_id, j.workflow_version, j.workflow_digest,
                          j.deadline_at, j.state AS job_state
                   FROM step_runs sr JOIN jobs j ON j.job_id = sr.job_id
                   WHERE sr.job_id = ? AND sr.step_id = ?""",
                (expired_step["job_id"], expired_step["step_id"]),
            ).fetchone()
            if step is None or step["state"] != "leased" or step["job_state"] in TERMINAL_JOBS:
                continue
            workflow = self.store.load_workflow(step["workflow_id"], step["workflow_version"], connection)
            definition = workflow.step(step["step_id"])
            killed, _ = self._is_killed(connection, step["workflow_id"])
            if not killed and step["attempts"] < step["max_attempts"] and now < step["deadline_at"]:
                delay = definition.retry.delay_after_failure(step["attempts"])
                available = utc_timestamp(now_value + timedelta(seconds=delay))
                next_step_version = step["version"] + 1
                connection.execute(
                    """UPDATE step_runs SET state = 'ready', version = version + 1, available_at = ?,
                           error = 'lease_expired', reserved_cost = 0,
                           lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, lease_fence_generation = NULL, updated_at = ?
                       WHERE job_id = ? AND step_id = ? AND state = 'leased'""",
                    (available, now, step["job_id"], step["step_id"]),
                )
                if step["approval_required"]:
                    connection.execute(
                        """UPDATE approvals SET step_version = ?
                           WHERE job_id = ? AND step_id = ? AND status = 'approved'""",
                        (next_step_version, step["job_id"], step["step_id"]),
                    )
                connection.execute(
                    """UPDATE jobs SET budget_reserved = budget_reserved - ?,
                           version = version + 1, updated_at = ? WHERE job_id = ?""",
                    (step["reserved_cost"], now, step["job_id"]),
                )
                self._sync_pending_approvals(connection, step["job_id"])
                self._refresh_job_state(connection, step["job_id"], now)
                if step["approval_required"]:
                    current_version = connection.execute(
                        "SELECT version FROM jobs WHERE job_id = ?", (step["job_id"],)
                    ).fetchone()["version"]
                    connection.execute(
                        """UPDATE approvals SET job_version = ?
                           WHERE job_id = ? AND step_id = ? AND status = 'approved'""",
                        (current_version, step["job_id"], step["step_id"]),
                    )
                event_type = "step.lease_recovered"
                retried += 1
            else:
                terminal = "cancelled" if killed else "failed"
                reason = "kill_switch" if killed else "lease_expired"
                connection.execute(
                    """UPDATE step_runs SET state = ?, version = version + 1, error = ?,
                           reserved_cost = 0, lease_owner = NULL, lease_token = NULL,
                           lease_expires_at = NULL, lease_fence_generation = NULL, updated_at = ?
                       WHERE job_id = ? AND step_id = ? AND state = 'leased'""",
                    (terminal, reason, now, step["job_id"], step["step_id"]),
                )
                connection.execute(
                    """UPDATE jobs SET budget_reserved = budget_reserved - ?,
                           version = version + 1, updated_at = ? WHERE job_id = ?""",
                    (step["reserved_cost"], now, step["job_id"]),
                )
                self._sync_pending_approvals(connection, step["job_id"])
                job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (step["job_id"],)).fetchone()
                self._promote_steps(connection, job, workflow, now)
                self._refresh_job_state(connection, step["job_id"], now)
                event_type = f"step.{terminal}"
                failed += 1
            self.store.append_event(
                connection,
                event_type=event_type,
                entity_type="step",
                entity_id=f"{step['job_id']}:{step['step_id']}",
                principal=principal,
                occurred_at=now,
                payload={"reason": "lease_expired", "attempt": step["attempts"]},
            )
        deadline_jobs = connection.execute(
            "SELECT * FROM jobs WHERE state NOT IN ('completed', 'failed', 'cancelled') AND deadline_at <= ?", (now,)
        ).fetchall()
        deadlines = 0
        for job in deadline_jobs:
            self._terminalize_job(
                connection,
                job["job_id"],
                state="failed",
                now=now,
                reason="job_deadline_exceeded",
                principal=principal,
                emit_event=False,
            )
            self.store.append_event(
                connection,
                event_type="job.deadline_exceeded",
                entity_type="job",
                entity_id=job["job_id"],
                principal=principal,
                occurred_at=now,
                payload={"deadline_at": job["deadline_at"]},
            )
            deadlines += 1
        outbox = connection.execute(
            """UPDATE outbox SET state = 'pending', lease_owner = NULL, lease_token = NULL,
                   lease_expires_at = NULL, available_at = ?
               WHERE state = 'leased' AND lease_expires_at <= ?""",
            (now, now),
        ).rowcount
        return {"leases_retried": retried, "leases_failed": failed, "deadlines_failed": deadlines, "outbox_recovered": outbox}

    def reconcile(self, *, principal: str = "admin") -> dict[str, int]:
        states_changed = 0
        outbox_created = 0
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, principal, "job:claim")
            jobs = connection.execute("SELECT * FROM jobs").fetchall()
            for job in jobs:
                before = job["state"]
                if before not in TERMINAL_JOBS:
                    workflow = self.store.load_workflow(job["workflow_id"], job["workflow_version"], connection)
                    self._promote_steps(connection, job, workflow, now)
                after = self._refresh_job_state(connection, job["job_id"], now)
                states_changed += int(before != after)
            missing = connection.execute(
                """SELECT e.* FROM events e LEFT JOIN outbox o ON o.event_sequence = e.sequence
                   WHERE o.sequence IS NULL ORDER BY e.sequence"""
            ).fetchall()
            for event in missing:
                envelope = canonical_json(
                    {
                        "event_sequence": event["sequence"],
                        "event_id": event["event_id"],
                        "event_type": event["event_type"],
                        "entity_type": event["entity_type"],
                        "entity_id": event["entity_id"],
                        "principal": event["principal"],
                        "occurred_at": event["occurred_at"],
                        "payload": json.loads(event["payload_json"]),
                        "event_hash": event["event_hash"],
                    }
                )
                connection.execute(
                    """INSERT INTO outbox(event_sequence, topic, payload_json, state, attempts, available_at, created_at)
                       VALUES (?, ?, ?, 'pending', 0, ?, ?)""",
                    (event["sequence"], f"control_plane.{event['event_type']}", envelope, now, now),
                )
                outbox_created += 1
        return {"job_states_changed": states_changed, "outbox_created": outbox_created}

    def claim_outbox(self, *, worker: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("lease_seconds is out of bounds")
        with self.store.transaction() as connection:
            now_value = self._now()
            now = utc_timestamp(now_value)
            self._require(connection, self.store, worker, "outbox:deliver")
            row = connection.execute(
                "SELECT * FROM outbox WHERE state = 'pending' AND available_at <= ? ORDER BY sequence LIMIT 1", (now,)
            ).fetchone()
            if row is None:
                return None
            lease_proof = secrets.token_hex(24)
            expires = utc_timestamp(now_value + timedelta(seconds=lease_seconds))
            connection.execute(
                """UPDATE outbox SET state = 'leased', attempts = attempts + 1,
                       lease_owner = ?, lease_token = ?, lease_expires_at = ? WHERE sequence = ? AND state = 'pending'""",
                (worker, lease_proof, expires, row["sequence"]),
            )
            return {
                "sequence": row["sequence"],
                "topic": row["topic"],
                "payload": json.loads(row["payload_json"]),
                "lease_token": lease_proof,
                "lease_expires_at": expires,
            }

    def acknowledge_outbox(self, sequence: int, lease_token: str, *, worker: str) -> None:
        with self.store.transaction() as connection:
            now = utc_timestamp(self._now())
            self._require(connection, self.store, worker, "outbox:deliver")
            updated = connection.execute(
                """UPDATE outbox SET state = 'delivered', delivered_at = ?, lease_owner = NULL,
                       lease_token = NULL, lease_expires_at = NULL
                   WHERE sequence = ? AND state = 'leased' AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?""",
                (now, sequence, worker, lease_token, now),
            )
            if updated.rowcount != 1:
                raise LeaseLostError("outbox lease no longer matches")

    def list_jobs(self, *, principal: str, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize_read(principal, "job:read")
        return self.store.list_jobs(state=state, limit=limit)

    def show_job(self, job_id: str, *, principal: str) -> dict[str, Any]:
        self._authorize_read(principal, "job:read")
        return self.store.get_job(job_id)

    def list_workflows(self, *, principal: str, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize_read(principal, "workflow:read")
        return self.store.list_workflows(limit=limit)

    def list_events(self, *, principal: str, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize_read(principal, "audit:read")
        return self.store.list_events(after=after, limit=limit)

    def list_outbox(self, *, principal: str, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize_read(principal, "outbox:read")
        return self.store.list_outbox(state=state, limit=limit)

    def list_kill_switches(self, *, principal: str, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize_read(principal, "kill:read")
        return self.store.list_kill_switches(limit=limit)

    def verify_audit(self, *, principal: str) -> dict[str, Any]:
        self._authorize_read(principal, "audit:read")
        return self.store.verify_audit()

    def _authorize_read(self, principal: str, capability: str) -> None:
        _bounded_text(principal, "principal")
        require(principal, self.store.capabilities(principal), capability)
