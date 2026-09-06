from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from model_router.models import (
    EvidenceBundle,
    MissionState,
    RouteDecision,
    TaskRequest,
)
from model_router.state_machine import InvalidTransition, validate_transition


class MissionNotFound(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class MissionRecord:
    mission_id: str
    task: TaskRequest
    decision: RouteDecision
    state: MissionState
    owner: str | None
    attempt: int
    max_attempts: int
    lease_owner: str | None
    lease_until: str | None
    last_error: str | None
    evidence: EvidenceBundle | None
    approval_required: bool
    approved: bool
    human_interventions: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "task": self.task.to_dict(),
            "decision": self.decision.to_dict(),
            "state": self.state.value,
            "owner": self.owner,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "last_error": self.last_error,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "approval_required": self.approval_required,
            "approved": self.approved,
            "human_interventions": self.human_interventions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MissionEvent:
    sequence: int
    mission_id: str
    from_state: MissionState | None
    to_state: MissionState
    actor: str
    reason: str
    evidence: EvidenceBundle | None
    occurred_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "mission_id": self.mission_id,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "actor": self.actor,
            "reason": self.reason,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "occurred_at": self.occurred_at,
        }


class SQLiteMissionStore:
    """Durable, idempotent mission queue with an append-only event trail."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._initialize()

    def _initialize(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                task_json TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                state TEXT NOT NULL,
                owner TEXT,
                attempt INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                lease_owner TEXT,
                lease_until TEXT,
                last_error TEXT,
                evidence_json TEXT,
                approval_required INTEGER NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 0,
                human_interventions INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mission_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT,
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_missions_claim
            ON missions(state, approved, created_at);

            CREATE INDEX IF NOT EXISTS idx_events_mission
            ON mission_events(mission_id, sequence);
            """
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteMissionStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def enqueue(
        self,
        task: TaskRequest,
        decision: RouteDecision,
        *,
        now: str | None = None,
    ) -> tuple[MissionRecord, bool]:
        timestamp = now or _utc_now()
        state = MissionState.REJECTED if decision.rejected else MissionState.QUEUED
        mission_id = f"mission-{uuid.uuid4().hex}"
        approval_required = decision.human_approval_required
        task_json = _json(task.to_dict())
        decision_json = _json(decision.to_dict())

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT * FROM missions WHERE idempotency_key = ?",
                (task.idempotency_key,),
            ).fetchone()
            if existing is not None:
                record = self._row_to_record(existing)
                if record.task != task:
                    raise ValueError(
                        f"idempotency conflict for key {task.idempotency_key}: payload differs"
                    )
                self._conn.execute("COMMIT")
                return record, False

            self._conn.execute(
                """
                INSERT INTO missions (
                    mission_id, idempotency_key, task_json, decision_json, state, owner,
                    attempt, max_attempts, approval_required, approved, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    task.idempotency_key,
                    task_json,
                    decision_json,
                    state.value,
                    decision.selected_owner,
                    task.max_attempts,
                    int(approval_required),
                    int(not approval_required),
                    timestamp,
                    timestamp,
                ),
            )
            reason = (
                "routing rejected: no eligible agent"
                if decision.rejected
                else "mission enqueued from routing decision"
            )
            self._insert_event(
                mission_id=mission_id,
                from_state=None,
                to_state=state,
                actor="router",
                reason=reason,
                occurred_at=timestamp,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(mission_id), True

    def get(self, mission_id: str) -> MissionRecord:
        row = self._conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        if row is None:
            raise MissionNotFound(mission_id)
        return self._row_to_record(row)

    def list(self, *, state: MissionState | None = None) -> list[MissionRecord]:
        if state is None:
            rows = self._conn.execute(
                "SELECT * FROM missions ORDER BY created_at, mission_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM missions WHERE state = ? ORDER BY created_at, mission_id",
                (state.value,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def claim(
        self,
        worker: str,
        *,
        lease_seconds: int = 300,
        now: str | None = None,
    ) -> MissionRecord | None:
        if not worker.strip():
            raise ValueError("worker must not be blank")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        timestamp = now or _utc_now()
        lease_until = _add_seconds(timestamp, lease_seconds)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                SELECT * FROM missions
                WHERE state = ? AND (approval_required = 0 OR approved = 1)
                ORDER BY created_at, mission_id
                LIMIT 1
                """,
                (MissionState.QUEUED.value,),
            ).fetchone()
            if row is None:
                self._conn.execute("COMMIT")
                return None
            current = self._row_to_record(row)
            validate_transition(current.state, MissionState.RUNNING)
            self._conn.execute(
                """
                UPDATE missions
                SET state = ?, lease_owner = ?, lease_until = ?, updated_at = ?
                WHERE mission_id = ?
                """,
                (
                    MissionState.RUNNING.value,
                    worker,
                    lease_until,
                    timestamp,
                    current.mission_id,
                ),
            )
            self._insert_event(
                mission_id=current.mission_id,
                from_state=current.state,
                to_state=MissionState.RUNNING,
                actor=worker,
                reason="mission claimed with bounded lease",
                occurred_at=timestamp,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(current.mission_id)

    def approve(
        self,
        mission_id: str,
        *,
        actor: str,
        now: str | None = None,
    ) -> MissionRecord:
        if not actor.strip():
            raise ValueError("actor must not be blank")
        timestamp = now or _utc_now()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._get_in_transaction(mission_id)
            if not current.approval_required:
                raise ValueError("mission does not require approval")
            if current.approved:
                self._conn.execute("COMMIT")
                return current
            self._conn.execute(
                """
                UPDATE missions
                SET approved = 1, human_interventions = human_interventions + 1, updated_at = ?
                WHERE mission_id = ?
                """,
                (timestamp, mission_id),
            )
            self._insert_event(
                mission_id=mission_id,
                from_state=current.state,
                to_state=current.state,
                actor=actor,
                reason="human approval recorded",
                occurred_at=timestamp,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(mission_id)

    def transition(
        self,
        mission_id: str,
        target: MissionState,
        *,
        actor: str,
        reason: str,
        evidence: EvidenceBundle | None = None,
        now: str | None = None,
    ) -> MissionRecord:
        if not actor.strip():
            raise ValueError("actor must not be blank")
        if target is MissionState.FAILED and not reason.strip():
            raise ValueError("failed transition requires a visible reason")
        if not reason.strip():
            raise ValueError("transition reason must not be blank")
        timestamp = now or _utc_now()

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._get_in_transaction(mission_id)
            if current.state is target:
                self._conn.execute("COMMIT")
                return current
            if target is MissionState.DONE and evidence is not None:
                missing = set(current.task.acceptance_criteria) - set(evidence.criteria)
                if missing:
                    raise InvalidTransition(
                        "evidence does not cover declared acceptance criteria: "
                        + ", ".join(sorted(missing))
                    )
            validate_transition(
                current.state,
                target,
                evidence=evidence,
                attempt=current.attempt,
                max_attempts=current.max_attempts,
            )
            lease_owner = actor if target is MissionState.RUNNING else None
            lease_until = _add_seconds(timestamp, 300) if target is MissionState.RUNNING else None
            last_error = reason if target is MissionState.FAILED else current.last_error
            evidence_json = _json(evidence.to_dict()) if evidence else None
            self._conn.execute(
                """
                UPDATE missions
                SET state = ?, lease_owner = ?, lease_until = ?, last_error = ?,
                    evidence_json = COALESCE(?, evidence_json), updated_at = ?
                WHERE mission_id = ?
                """,
                (
                    target.value,
                    lease_owner,
                    lease_until,
                    last_error,
                    evidence_json,
                    timestamp,
                    mission_id,
                ),
            )
            self._insert_event(
                mission_id=mission_id,
                from_state=current.state,
                to_state=target,
                actor=actor,
                reason=reason,
                evidence=evidence,
                occurred_at=timestamp,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(mission_id)

    def retry(
        self,
        mission_id: str,
        *,
        actor: str,
        now: str | None = None,
    ) -> MissionRecord:
        if not actor.strip():
            raise ValueError("actor must not be blank")
        timestamp = now or _utc_now()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._get_in_transaction(mission_id)
            validate_transition(
                current.state,
                MissionState.QUEUED,
                attempt=current.attempt,
                max_attempts=current.max_attempts,
            )
            self._conn.execute(
                """
                UPDATE missions
                SET state = ?, attempt = attempt + 1, lease_owner = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE mission_id = ?
                """,
                (MissionState.QUEUED.value, timestamp, mission_id),
            )
            self._insert_event(
                mission_id=mission_id,
                from_state=current.state,
                to_state=MissionState.QUEUED,
                actor=actor,
                reason=f"retry scheduled for attempt {current.attempt + 1}",
                occurred_at=timestamp,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get(mission_id)

    def recover_expired(
        self,
        *,
        actor: str,
        now: str | None = None,
    ) -> list[MissionRecord]:
        """Move expired leased work to a visible failure state exactly once."""
        if not actor.strip():
            raise ValueError("actor must not be blank")
        timestamp = now or _utc_now()
        recovered_ids: list[str] = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                """
                SELECT * FROM missions
                WHERE state = ? AND lease_until IS NOT NULL AND lease_until <= ?
                ORDER BY lease_until, mission_id
                """,
                (MissionState.RUNNING.value, timestamp),
            ).fetchall()
            for row in rows:
                current = self._row_to_record(row)
                validate_transition(current.state, MissionState.FAILED)
                self._conn.execute(
                    """
                    UPDATE missions
                    SET state = ?, lease_owner = NULL, lease_until = NULL,
                        last_error = ?, updated_at = ?
                    WHERE mission_id = ?
                    """,
                    (
                        MissionState.FAILED.value,
                        "worker lease expired",
                        timestamp,
                        current.mission_id,
                    ),
                )
                self._insert_event(
                    mission_id=current.mission_id,
                    from_state=current.state,
                    to_state=MissionState.FAILED,
                    actor=actor,
                    reason="worker lease expired",
                    occurred_at=timestamp,
                )
                recovered_ids.append(current.mission_id)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return [self.get(mission_id) for mission_id in recovered_ids]

    def events(self, mission_id: str) -> list[MissionEvent]:
        self.get(mission_id)
        rows = self._conn.execute(
            "SELECT * FROM mission_events WHERE mission_id = ? ORDER BY sequence",
            (mission_id,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def metrics(self) -> dict[str, int | float]:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN state = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                SUM(CASE WHEN state = 'done' AND attempt = 1 THEN 1 ELSE 0 END) AS pass_at_1,
                SUM(attempt - 1) AS retries,
                SUM(human_interventions) AS interventions
            FROM missions
            """
        ).fetchone()
        total = int(row["total"] or 0)
        rejected = int(row["rejected"] or 0)
        terminal_rows = self._conn.execute(
            """
            SELECT created_at, updated_at FROM missions
            WHERE state IN (?, ?)
            """,
            (MissionState.DONE.value, MissionState.REJECTED.value),
        ).fetchall()
        wall_times = [
            _seconds_between(str(item["created_at"]), str(item["updated_at"]))
            for item in terminal_rows
        ]
        return {
            "total_missions": total,
            "done_missions": int(row["done"] or 0),
            "failed_missions": int(row["failed"] or 0),
            "rejected_missions": rejected,
            "pass_at_1": round((int(row["pass_at_1"] or 0) / total), 6) if total else 0.0,
            "average_retries_per_task": (
                round((int(row["retries"] or 0) / total), 6) if total else 0.0
            ),
            "rejection_rate": round((rejected / total), 6) if total else 0.0,
            "human_interventions": int(row["interventions"] or 0),
            "average_wall_time_seconds": (
                round(sum(wall_times) / len(wall_times), 6) if wall_times else 0.0
            ),
        }

    def _get_in_transaction(self, mission_id: str) -> MissionRecord:
        row = self._conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        if row is None:
            raise MissionNotFound(mission_id)
        return self._row_to_record(row)

    def _insert_event(
        self,
        *,
        mission_id: str,
        from_state: MissionState | None,
        to_state: MissionState,
        actor: str,
        reason: str,
        occurred_at: str,
        evidence: EvidenceBundle | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO mission_events (
                mission_id, from_state, to_state, actor, reason, evidence_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                from_state.value if from_state else None,
                to_state.value,
                actor,
                reason,
                _json(evidence.to_dict()) if evidence else None,
                occurred_at,
            ),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MissionRecord:
        evidence_payload = json.loads(row["evidence_json"]) if row["evidence_json"] else None
        return MissionRecord(
            mission_id=str(row["mission_id"]),
            task=TaskRequest.from_dict(json.loads(row["task_json"])),
            decision=RouteDecision.from_dict(json.loads(row["decision_json"])),
            state=MissionState(str(row["state"])),
            owner=str(row["owner"]) if row["owner"] is not None else None,
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            lease_owner=(
                str(row["lease_owner"]) if row["lease_owner"] is not None else None
            ),
            lease_until=(
                str(row["lease_until"]) if row["lease_until"] is not None else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
            evidence=EvidenceBundle.from_dict(evidence_payload) if evidence_payload else None,
            approval_required=bool(row["approval_required"]),
            approved=bool(row["approved"]),
            human_interventions=int(row["human_interventions"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> MissionEvent:
        evidence_payload = json.loads(row["evidence_json"]) if row["evidence_json"] else None
        return MissionEvent(
            sequence=int(row["sequence"]),
            mission_id=str(row["mission_id"]),
            from_state=(
                MissionState(str(row["from_state"]))
                if row["from_state"] is not None
                else None
            ),
            to_state=MissionState(str(row["to_state"])),
            actor=str(row["actor"]),
            reason=str(row["reason"]),
            evidence=EvidenceBundle.from_dict(evidence_payload) if evidence_payload else None,
            occurred_at=str(row["occurred_at"]),
        )


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _add_seconds(timestamp: str, seconds: int) -> str:
    value = datetime.fromisoformat(timestamp)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (value + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def _seconds_between(start: str, end: str) -> float:
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
