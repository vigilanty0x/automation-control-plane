"""Attempt-bound, declarative approvals in the native Factory SQLite store.

This module adds policy records, never another task queue or actor authority.
The caller is a trusted local operator; a declared actor is not authenticated.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import math
import re

from .models import FactorySpec
from .state import RunState

MAX_APPROVAL_SECONDS = 86400
MAX_DECISIONS_PER_RUN = 1000
APPROVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_runtime (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
 observed_at REAL NOT NULL, paused_since REAL, paused_seconds REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approval_decisions (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES runs(run_id), task_id TEXT NOT NULL,
 attempt INTEGER NOT NULL, decision_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 decision_json TEXT NOT NULL, decision_sha256 TEXT NOT NULL,
 UNIQUE(run_id, decision_id),
 FOREIGN KEY(run_id,task_id) REFERENCES tasks(run_id,task_id)
);
CREATE INDEX IF NOT EXISTS approval_attempt ON approval_decisions(run_id,task_id,attempt,sequence);
"""


class ApprovalError(ValueError):
    """Approval cannot authorize this exact attempt at the observed time."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ApprovalError("approval timestamp must be finite epoch seconds in range")
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}", value):
        raise ApprovalError(f"{name} must be a bounded printable identifier")
    return value


def request_for_spec(spec, run_id, task_id, attempt):
    """Canonical review material, independent of mutable scheduling state."""
    try:
        task = spec.task(task_id)
    except KeyError as exc:
        raise ApprovalError("unknown approval task") from exc
    if task.approval != "required":
        raise ApprovalError("task does not require approval")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 100:
        raise ApprovalError("attempt must be an integer in 1..100")
    contract = next(item for item in spec.to_dict()["tasks"] if item["id"] == task_id)
    request = {"format": "factory-approval-request-v1", "run_id": run_id,
               "spec_sha256": sha256(spec.canonical_json().encode("utf-8")).hexdigest(),
               "task_id": task_id, "attempt": attempt, "task": contract,
               "budget": spec.to_dict()["budget"], "workspace": spec.workspace}
    return {**request, "request_sha256": _digest(request)}


class ApprovalStoreMixin:
    """Methods share FactoryStore's connection and transactional event writer."""

    def _approval_spec(self, connection, run_id):
        row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ApprovalError("unknown run")
        spec = FactorySpec.from_json(row["spec_json"])
        if sha256(spec.canonical_json().encode("utf-8")).hexdigest() != row["spec_hash"]:
            raise ApprovalError("stored specification digest mismatch")
        return row, spec

    def _approval_clock(self, connection, run_id, now, *, update=False):
        row = connection.execute("SELECT * FROM approval_runtime WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            _, spec = self._approval_spec(connection, run_id)
            if any(task.approval == "required" for task in spec.tasks) or spec.budget.execution_quota is not None:
                raise ApprovalError("approval runtime record is missing")
            return None
        now = _time(now)
        if now < _time(row["observed_at"]):
            raise ApprovalError("approval clock regressed")
        _time(row["paused_seconds"])
        if row["paused_since"] is not None and not _time(row["paused_since"]) <= now:
            raise ApprovalError("approval pause is in the future")
        if update:
            connection.execute("UPDATE approval_runtime SET observed_at=? WHERE run_id=?", (now, run_id))
        return row

    def _approval_request(self, connection, run_id, task_id, attempt):
        run, spec = self._approval_spec(connection, run_id)
        return request_for_spec(spec, run_id, task_id, attempt)

    def approval_request(self, run_id, task_id):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT attempts,state,max_attempts FROM tasks WHERE run_id=? AND task_id=?", (run_id, task_id)).fetchone()
            if row is None or row["state"] != "ready" or row["attempts"] >= row["max_attempts"]:
                raise ApprovalError("task has no approvable next attempt")
            self._approval_clock(connection, run_id, self.clock())
            return self._approval_request(connection, run_id, task_id, row["attempts"] + 1)

    def record_approval(self, run_id, task_id, *, attempt, request_sha256, decision,
                        decided_by, expires_at, decision_id):
        _text(decided_by, "decided_by")
        _text(decision_id, "decision_id")
        if not isinstance(decision, str) or decision not in {"approved", "rejected"}:
            raise ApprovalError("decision must be approved or rejected")
        if not isinstance(request_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
            raise ApprovalError("request_sha256 must be a SHA256 digest")
        expires_at = _time(expires_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _time(self.clock())
            self._approval_clock(connection, run_id, now, update=True)
            request = self._approval_request(connection, run_id, task_id, attempt)
            if request["request_sha256"] != request_sha256:
                raise ApprovalError("approval request digest mismatch")
            supplied = {"run_id": run_id, "task_id": task_id, "attempt": attempt,
                        "request_sha256": request_sha256, "decision": decision,
                        "decided_by": decided_by, "expires_at": expires_at, "decision_id": decision_id}
            old = connection.execute("SELECT * FROM approval_decisions WHERE run_id=? AND decision_id=?", (run_id, decision_id)).fetchone()
            if old is not None:
                value = self._decode_decision(old)
                if any(value.get(key) != item for key, item in supplied.items()):
                    raise ApprovalError("decision_id already has different content")
                connection.commit()
                return value
            run = connection.execute("SELECT state,kill_switch FROM runs WHERE run_id=?", (run_id,)).fetchone()
            row = connection.execute("SELECT attempts,state,max_attempts FROM tasks WHERE run_id=? AND task_id=?", (run_id, task_id)).fetchone()
            if run["kill_switch"] or run["state"] not in {RunState.CREATED, RunState.RUNNING} or row["state"] != "ready" or row["attempts"] + 1 != attempt or attempt > row["max_attempts"]:
                raise ApprovalError("approval attempt is no longer pending")
            if not now < expires_at <= now + MAX_APPROVAL_SECONDS:
                raise ApprovalError("approval expiry must be future and within 86400 seconds")
            count = connection.execute("SELECT COUNT(*) FROM approval_decisions WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= MAX_DECISIONS_PER_RUN:
                raise ApprovalError("approval retention limit reached; records are preserved")
            value = {"format": "factory-approval-decision-v1", **supplied, "decided_at": now,
                     "actor_authentication": "not_established"}
            digest = _digest(value)
            connection.execute("INSERT INTO approval_decisions(run_id,task_id,attempt,decision_id,request_sha256,decision_json,decision_sha256) VALUES(?,?,?,?,?,?,?)",
                               (run_id, task_id, attempt, decision_id, request_sha256, _json(value), digest))
            self._event(connection, run_id=run_id, task_id=task_id, event_type="approval.decided", created_at=now,
                        payload={"decision": value, "decision_sha256": digest},
                        event_key=f"approval.decided:{decision_id}")
            connection.commit()
            return value
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _decode_decision(row):
        try:
            value = json.loads(row["decision_json"])
            if not isinstance(value, dict) or set(value) != {"format", "run_id", "task_id", "attempt", "request_sha256", "decision", "decided_by", "expires_at", "decision_id", "decided_at", "actor_authentication"}:
                raise ValueError("fields")
            if _digest(value) != row["decision_sha256"] or _json(value) != row["decision_json"]:
                raise ValueError("digest")
            for name in ("run_id", "task_id", "attempt", "decision_id", "request_sha256"):
                if value[name] != row[name]:
                    raise ValueError("identity")
            if value["format"] != "factory-approval-decision-v1" or value["actor_authentication"] != "not_established" or value["decision"] not in {"approved", "rejected"}:
                raise ValueError("contract")
            if isinstance(value["attempt"], bool) or not isinstance(value["attempt"], int) or not 1 <= value["attempt"] <= 100:
                raise ValueError("attempt")
            _text(value["decided_by"], "decided_by")
            _text(value["decision_id"], "decision_id")
            start, end = _time(value["decided_at"]), _time(value["expires_at"])
            if not start < end <= start + MAX_APPROVAL_SECONDS:
                raise ValueError("dates")
            return value
        except (ValueError, TypeError, KeyError) as exc:
            raise ApprovalError("stored approval is invalid") from exc

    def _approval_check(self, connection, run_id, task_id, attempt, now):
        # Only protected runs enter this path; old runs keep their wire format.
        self._approval_clock(connection, run_id, now)
        request = self._approval_request(connection, run_id, task_id, attempt)
        row = connection.execute("SELECT * FROM approval_decisions WHERE run_id=? AND task_id=? AND attempt=? ORDER BY sequence DESC LIMIT 1", (run_id, task_id, attempt)).fetchone()
        if row is None:
            return "missing", None
        value = self._decode_decision(row)
        if value["request_sha256"] != request["request_sha256"]:
            raise ApprovalError("stored approval request digest mismatch")
        event = connection.execute("SELECT payload_json FROM events WHERE run_id=? AND event_key=?", (run_id, f"approval.decided:{value['decision_id']}")).fetchone()
        if event is None or json.loads(event["payload_json"]) != {"decision": value, "decision_sha256": row["decision_sha256"]}:
            raise ApprovalError("approval does not match its journal event")
        self._replay_with_connection(connection, run_id)
        if now < value["decided_at"]:
            raise ApprovalError("approval was decided in the future")
        if now >= value["expires_at"]:
            return "expired", value
        return value["decision"], value

    def _approval_gate(self, connection, run_id, task_id, attempt, now):
        runtime = self._approval_clock(connection, run_id, now, update=True)
        if runtime is None:
            return None
        _, spec = self._approval_spec(connection, run_id)
        if spec.task(task_id).approval != "required":
            return None
        status, value = self._approval_check(connection, run_id, task_id, attempt, now)
        if status != "approved":
            raise ApprovalError(f"approval {status}")
        return value

    def approval_seconds_remaining(self, claim):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _time(self.clock())
            value = self._approval_gate(connection, claim.run_id, claim.task_id, claim.attempt, now)
            connection.commit()
            return None if value is None else value["expires_at"] - now

    def _approval_pause(self, connection, run_id, now, paused):
        runtime = self._approval_clock(connection, run_id, now, update=True)
        if runtime is None:
            return
        # Reuse one active-wall clock for approval and execution-quota waits.
        _, spec = self._approval_spec(connection, run_id)
        prefix = "execution" if spec.budget.execution_quota is not None else "approval"
        if paused and runtime["paused_since"] is None:
            connection.execute("UPDATE approval_runtime SET paused_since=? WHERE run_id=?", (now, run_id))
            self._event(connection, run_id=run_id, task_id=None, event_type=f"{prefix}.wait_started", created_at=now,
                        payload={"at": now}, event_key=f"{prefix}.wait_started:{self._approval_event_count(connection,run_id)}")
        elif not paused and runtime["paused_since"] is not None:
            duration = now - runtime["paused_since"]
            connection.execute("UPDATE approval_runtime SET paused_since=NULL,paused_seconds=paused_seconds+? WHERE run_id=?", (duration, run_id))
            self._event(connection, run_id=run_id, task_id=None, event_type=f"{prefix}.wait_finished", created_at=now,
                        payload={"at": now, "seconds": duration}, event_key=f"{prefix}.wait_finished:{self._approval_event_count(connection,run_id)}")

    @staticmethod
    def _approval_event_count(connection, run_id):
        return connection.execute("SELECT event_count FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]

    def _approval_elapsed(self, connection, run, now):
        runtime = self._approval_clock(connection, run["run_id"], now)
        end = run["finished_at"] if run["finished_at"] is not None else now
        elapsed = max(0.0, end - run["started_at"]) if run["started_at"] is not None else 0.0
        if runtime is not None and run["started_at"] is not None:
            paused = runtime["paused_seconds"]
            if runtime["paused_since"] is not None:
                paused += max(0.0, end - runtime["paused_since"])
            elapsed = max(0.0, elapsed - paused)
        return elapsed

    def _approval_snapshot(self, connection, run, task_items, now):
        if self._approval_clock(connection, run["run_id"], now) is None:
            return {}
        _, spec = self._approval_spec(connection, run["run_id"])
        waiting = []
        executable = False
        for task in task_items:
            if task["state"] == "running":
                executable = True
            if task["state"] != "ready":
                continue
            if spec.task(task["task_id"]).approval != "required":
                executable = True
                continue
            status, value = self._approval_check(connection, run["run_id"], task["task_id"], task["attempts"] + 1, now)
            if status == "approved":
                executable = True
            else:
                waiting.append({"task_id": task["task_id"], "attempt": task["attempts"] + 1, "approval": status})
        return {"waiting_for_approval": waiting,
                "execution_status": "waiting_approval" if waiting and not executable else run["state"],
                "active_wall_seconds": self._approval_elapsed(connection, run, now),
                "approval_actor_authentication": "not_established"}


def verify_approval_evidence(spec, run_id, events, receipts):
    """Check recorded authorization coherence; does not authenticate its actor."""
    from .executors import command_digest

    issues = []
    for item in receipts:
        try:
            task_id, attempt = item["task_id"], item["attempt"]
            task = spec.task(task_id)
            if task.approval != "required":
                continue
            claim_index = next(i for i, event in enumerate(events) if event.get("event_key") == f"task.claimed:{task_id}:{attempt}")
            completion_index = next(i for i, event in enumerate(events) if event.get("event_key") == f"task.completed:{task_id}:{attempt}")
            candidates = [event for event in events[:claim_index] if event.get("event_type") == "approval.decided"
                          and isinstance(event.get("payload"), dict)
                          and isinstance(event["payload"].get("decision"), dict)
                          and event["payload"]["decision"].get("task_id") == task_id
                          and event["payload"]["decision"].get("attempt") == attempt]
            event = candidates[-1]
            value = event["payload"]["decision"]
            # The wire document is validated by the same pure decoder as SQLite.
            row = {**value, "decision_json": _json(value), "decision_sha256": event["payload"].get("decision_sha256")}
            value = ApprovalStoreMixin._decode_decision(row)
            request = request_for_spec(spec, run_id, task_id, attempt)
            claim_at = _time(events[claim_index]["created_at"])
            complete_at = _time(events[completion_index]["created_at"])
            receipt = item["receipt"]
            execution = receipt["execution"]
            if execution["command_digest"] != command_digest(task.command) or execution["label"] != task.id or receipt["owner"] != task.owner:
                raise ApprovalError("receipt execution differs from reviewed task")
            if len(receipt["tests"]) != len(task.tests):
                raise ApprovalError("receipt tests differ from reviewed task")
            for recorded_test, test in zip(receipt["tests"], task.tests):
                if recorded_test["name"] != test.name:
                    raise ApprovalError("receipt tests differ from reviewed task")
                if recorded_test.get("status") != "not_run" and (recorded_test["command_digest"] != command_digest(test.command) or recorded_test["label"] != f"{task.id}:test:{test.name}"):
                    raise ApprovalError("receipt test command differs from reviewed task")
            if not (value["run_id"] == run_id and value["decision"] == "approved"
                    and value["request_sha256"] == request["request_sha256"]
                    and event.get("task_id") == task_id and event.get("event_key") == f"approval.decided:{value['decision_id']}"
                    and _time(event["created_at"]) == value["decided_at"]
                    and value["decided_at"] <= claim_at <= _time(receipt["started_at"]) <= _time(receipt["finished_at"]) <= complete_at < value["expires_at"]
                    and claim_index < completion_index):
                raise ApprovalError("approval binding or dates do not cover the attempt")
        except (ApprovalError, ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError):
            issues.append("protected receipt lacks a valid bound approval")
    return issues
