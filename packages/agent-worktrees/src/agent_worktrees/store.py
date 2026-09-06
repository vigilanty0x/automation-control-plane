from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import EvidenceBundle, MissionRecord, MissionRequest, MissionState
from .state_machine import InvalidTransition, validate_transition


class MissionNotFound(KeyError):
    """Raised when a mission identifier is unknown."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def slug(value: str, *, fallback: str = "item", limit: int = 32) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (normalized or fallback)[:limit].rstrip("-")


def paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


class SQLiteMissionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SQLiteMissionStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL,
                request_json TEXT NOT NULL,
                state TEXT NOT NULL,
                repo_root TEXT NOT NULL,
                branch TEXT NOT NULL UNIQUE,
                worktree_path TEXT NOT NULL UNIQUE,
                attempt INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                last_error TEXT,
                evidence_json TEXT,
                cleaned_at TEXT,
                human_interventions INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mission_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT,
                details_json TEXT,
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS missions_state_idx ON missions(state, created_at);
            CREATE INDEX IF NOT EXISTS events_mission_idx ON mission_events(mission_id, sequence);
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    @staticmethod
    def _fingerprint(request: MissionRequest, repo_root: Path, worktree_root: Path) -> str:
        payload = {
            "request": request.to_dict(),
            "repo_root": str(repo_root),
            "worktree_root": str(worktree_root),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def register(
        self,
        request: MissionRequest,
        *,
        repo_root: str | Path,
        worktree_root: str | Path,
        now: str | None = None,
    ) -> tuple[MissionRecord, bool]:
        timestamp = now or utc_now()
        repository = Path(repo_root).resolve()
        worktrees = Path(worktree_root).resolve()
        fingerprint = self._fingerprint(request, repository, worktrees)
        with self._transaction():
            existing = self.connection.execute(
                "SELECT * FROM missions WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("idempotency conflict: key already identifies different work")
                return self._record(existing), False

            conflict = self._find_conflict(request, repository)
            mission_id = f"mission-{uuid.uuid4().hex}"
            branch = (
                f"agent/{slug(request.agent_id)}/{slug(request.task_id)}-"
                f"{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:8]}"
            )
            worktree_path = worktrees / slug(request.agent_id) / mission_id
            state = MissionState.REJECTED if conflict else MissionState.QUEUED
            last_error = conflict
            self.connection.execute(
                """
                INSERT INTO missions (
                    mission_id, idempotency_key, fingerprint, request_json, state,
                    repo_root, branch, worktree_path, attempt, max_attempts,
                    last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    request.idempotency_key,
                    fingerprint,
                    json.dumps(request.to_dict(), sort_keys=True),
                    state.value,
                    str(repository),
                    branch,
                    str(worktree_path),
                    request.max_attempts,
                    last_error,
                    timestamp,
                    timestamp,
                ),
            )
            reason = last_error or "mission registered with exclusive path ownership"
            self._append_event(
                mission_id,
                None,
                state,
                actor="registry",
                reason=reason,
                occurred_at=timestamp,
            )
            return self.get(mission_id), True

    def _find_conflict(self, request: MissionRequest, repo_root: Path) -> str | None:
        rows = self.connection.execute(
            """
            SELECT mission_id, request_json
            FROM missions
            WHERE repo_root = ? AND cleaned_at IS NULL AND state != ?
            ORDER BY created_at, mission_id
            """,
            (str(repo_root), MissionState.REJECTED.value),
        ).fetchall()
        for row in rows:
            other = MissionRequest.from_dict(json.loads(row["request_json"]))
            for desired in request.owned_paths:
                for held in other.owned_paths:
                    if paths_overlap(desired, held):
                        return (
                            f"ownership conflict with {row['mission_id']}: "
                            f"requested {desired} overlaps {held}"
                        )
        return None

    def get(self, mission_id: str) -> MissionRecord:
        row = self.connection.execute(
            "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        if row is None:
            raise MissionNotFound(mission_id)
        return self._record(row)

    def list(self, state: MissionState | None = None) -> list[MissionRecord]:
        if state is None:
            rows = self.connection.execute(
                "SELECT * FROM missions ORDER BY created_at, mission_id"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM missions WHERE state = ? ORDER BY created_at, mission_id",
                (state.value,),
            ).fetchall()
        return [self._record(row) for row in rows]

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
        actor = actor.strip()
        reason = reason.strip()
        if not actor:
            raise ValueError("actor is required")
        if not reason:
            raise ValueError("reason is required")
        timestamp = now or utc_now()
        with self._transaction():
            current = self.get(mission_id)
            validate_transition(
                current.state,
                target,
                evidence=evidence,
                declared_criteria=current.request.acceptance_criteria,
                attempt=current.attempt,
                max_attempts=current.max_attempts,
            )
            if current.state == target:
                return current
            last_error = reason if target == MissionState.FAILED else current.last_error
            evidence_json = None if evidence is None else json.dumps(evidence.to_dict(), sort_keys=True)
            self.connection.execute(
                """
                UPDATE missions
                SET state = ?, last_error = ?, evidence_json = COALESCE(?, evidence_json), updated_at = ?
                WHERE mission_id = ?
                """,
                (target.value, last_error, evidence_json, timestamp, mission_id),
            )
            self._append_event(
                mission_id,
                current.state,
                target,
                actor=actor,
                reason=reason,
                occurred_at=timestamp,
                evidence=evidence,
            )
            return self.get(mission_id)

    def retry(self, mission_id: str, *, actor: str, now: str | None = None) -> MissionRecord:
        actor = actor.strip()
        if not actor:
            raise ValueError("actor is required")
        timestamp = now or utc_now()
        with self._transaction():
            current = self.get(mission_id)
            validate_transition(
                current.state,
                MissionState.QUEUED,
                attempt=current.attempt,
                max_attempts=current.max_attempts,
            )
            self.connection.execute(
                """
                UPDATE missions
                SET state = ?, attempt = attempt + 1, last_error = NULL, updated_at = ?
                WHERE mission_id = ?
                """,
                (MissionState.QUEUED.value, timestamp, mission_id),
            )
            self._append_event(
                mission_id,
                current.state,
                MissionState.QUEUED,
                actor=actor,
                reason="bounded retry reuses the existing branch and worktree",
                occurred_at=timestamp,
            )
            return self.get(mission_id)

    def mark_cleaned(
        self, mission_id: str, *, actor: str, now: str | None = None
    ) -> MissionRecord:
        timestamp = now or utc_now()
        with self._transaction():
            current = self.get(mission_id)
            if current.state != MissionState.DONE:
                raise InvalidTransition("cleanup requires a done mission")
            if current.cleaned:
                return current
            self.connection.execute(
                "UPDATE missions SET cleaned_at = ?, updated_at = ? WHERE mission_id = ?",
                (timestamp, timestamp, mission_id),
            )
            self._append_event(
                mission_id,
                current.state,
                current.state,
                actor=actor,
                reason="integrated worktree and branch cleaned safely",
                occurred_at=timestamp,
                details={"cleaned": True},
            )
            return self.get(mission_id)

    def record_intervention(
        self, mission_id: str, *, actor: str, reason: str, now: str | None = None
    ) -> MissionRecord:
        timestamp = now or utc_now()
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and reason are required")
        with self._transaction():
            current = self.get(mission_id)
            self.connection.execute(
                """
                UPDATE missions
                SET human_interventions = human_interventions + 1, updated_at = ?
                WHERE mission_id = ?
                """,
                (timestamp, mission_id),
            )
            self._append_event(
                mission_id,
                current.state,
                current.state,
                actor=actor,
                reason=reason,
                occurred_at=timestamp,
                details={"human_intervention": True},
            )
            return self.get(mission_id)

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        self.get(mission_id)
        rows = self.connection.execute(
            "SELECT * FROM mission_events WHERE mission_id = ? ORDER BY sequence",
            (mission_id,),
        ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "mission_id": row["mission_id"],
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "actor": row["actor"],
                "reason": row["reason"],
                "evidence": None if row["evidence_json"] is None else json.loads(row["evidence_json"]),
                "details": None if row["details_json"] is None else json.loads(row["details_json"]),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]

    def metrics(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM missions").fetchall()
        total = len(rows)
        actionable = [row for row in rows if row["state"] != MissionState.REJECTED.value]
        done = [row for row in rows if row["state"] == MissionState.DONE.value]
        rejected = total - len(actionable)
        retries = sum(max(0, row["attempt"] - 1) for row in actionable)
        durations = []
        for row in done:
            created = datetime.fromisoformat(row["created_at"])
            updated = datetime.fromisoformat(row["updated_at"])
            durations.append((updated - created).total_seconds())
        by_state = {state.value: 0 for state in MissionState}
        for row in rows:
            by_state[row["state"]] += 1
        return {
            "total_missions": total,
            "by_state": by_state,
            "cleaned_missions": sum(row["cleaned_at"] is not None for row in rows),
            "pass_at_1": 0.0
            if not actionable
            else sum(
                row["state"] == "done" and row["attempt"] == 1 for row in rows
            )
            / len(actionable),
            "average_retries_per_task": 0.0 if not actionable else retries / len(actionable),
            "rejection_rate": 0.0 if not total else rejected / total,
            "human_interventions": sum(row["human_interventions"] for row in rows),
            "average_wall_time_seconds": 0.0 if not durations else sum(durations) / len(durations),
        }

    def _append_event(
        self,
        mission_id: str,
        source: MissionState | None,
        target: MissionState,
        *,
        actor: str,
        reason: str,
        occurred_at: str,
        evidence: EvidenceBundle | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO mission_events (
                mission_id, from_state, to_state, actor, reason,
                evidence_json, details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                None if source is None else source.value,
                target.value,
                actor,
                reason,
                None if evidence is None else json.dumps(evidence.to_dict(), sort_keys=True),
                None if details is None else json.dumps(details, sort_keys=True),
                occurred_at,
            ),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MissionRecord:
        evidence = None
        if row["evidence_json"] is not None:
            evidence = EvidenceBundle.from_dict(json.loads(row["evidence_json"]))
        return MissionRecord(
            mission_id=row["mission_id"],
            request=MissionRequest.from_dict(json.loads(row["request_json"])),
            state=MissionState(row["state"]),
            repo_root=row["repo_root"],
            branch=row["branch"],
            worktree_path=row["worktree_path"],
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            last_error=row["last_error"],
            evidence=evidence,
            cleaned_at=row["cleaned_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
