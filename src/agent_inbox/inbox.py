"""Atomic SQLite mission queue with leases, retries, and append-only events."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Callable, Iterator, Mapping

from .contract import AgentProfile, CompletionEvidence, MissionSpec, MissionStatus, canonical_json, sha256_json


class InboxError(RuntimeError): pass
class IdempotencyConflict(InboxError): pass
class MissionNotFound(InboxError): pass
class NoMissionAvailable(InboxError): pass
class CapabilityMismatch(InboxError): pass
class LeaseConflict(InboxError): pass
class StateConflict(InboxError): pass
class EvidenceRequired(InboxError): pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, active INTEGER NOT NULL,
  max_running INTEGER NOT NULL, max_lease_seconds INTEGER NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS missions (
  mission_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
  logical_sha256 TEXT NOT NULL, spec_json TEXT NOT NULL, title TEXT NOT NULL,
  priority INTEGER NOT NULL, owner_scope TEXT NOT NULL, required_capabilities_json TEXT NOT NULL,
  required_permissions_json TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL, lease_owner TEXT, lease_token TEXT, lease_expires_at REAL,
  waiting_reason TEXT, failure_reason TEXT, evidence_json TEXT, evidence_sha256 TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS missions_queue_order ON missions(status, priority DESC, created_at, mission_id);
CREATE INDEX IF NOT EXISTS missions_lease_expiry ON missions(status, lease_expires_at);
CREATE TABLE IF NOT EXISTS events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id), kind TEXT NOT NULL,
  actor TEXT NOT NULL, detail_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_mission ON events(mission_id, sequence);
"""


def _iso(epoch: float | None) -> str | None:
    if epoch is None: return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise StateConflict(f"{name} must be a non-empty string up to 4096 characters")
    return value.strip()


class AgentInbox:
    def __init__(
        self, path: str | os.PathLike[str], *, clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(24),
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.token_factory = token_factory

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            connection.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version','1.0')")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def register_agent(self, profile: AgentProfile) -> dict[str, object]:
        now = self.clock(); data = canonical_json(profile.to_dict())
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO agents(agent_id,profile_json,active,max_running,max_lease_seconds,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
                profile_json=excluded.profile_json,active=excluded.active,max_running=excluded.max_running,
                max_lease_seconds=excluded.max_lease_seconds,updated_at=excluded.updated_at""",
                (profile.agent_id, data, profile.active, profile.max_running, profile.max_lease_seconds, now),
            )
        return {**profile.to_dict(), "updated_at": _iso(now), "profile_sha256": sha256_json(profile.to_dict())}

    def enqueue(self, spec: MissionSpec) -> dict[str, object]:
        now = self.clock(); logical = spec.logical_sha256
        mission_id = hashlib.sha256(f"mission:{spec.idempotency_key}".encode()).hexdigest()[:32]
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM missions WHERE idempotency_key=?", (spec.idempotency_key,)).fetchone()
            if existing:
                if existing["logical_sha256"] != logical:
                    raise IdempotencyConflict("idempotency key already has different logical content")
                return self._mission(connection, existing)
            connection.execute(
                """INSERT INTO missions(
                mission_id,idempotency_key,logical_sha256,spec_json,title,priority,owner_scope,
                required_capabilities_json,required_permissions_json,status,max_retries,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mission_id, spec.idempotency_key, logical, canonical_json(spec.to_dict()), spec.title,
                 spec.priority, spec.owner_scope, canonical_json(list(spec.required_capabilities)),
                 canonical_json(list(spec.required_permissions)), MissionStatus.QUEUED.value,
                 spec.max_retries, now, now),
            )
            self._event(connection, mission_id, f"enqueue:{spec.idempotency_key}", "queued", "system", {"logical_sha256": logical}, now)
            row = connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            return self._mission(connection, row)

    def claim(self, agent_id: str, *, lease_seconds: int = 60) -> dict[str, object]:
        agent_id = _bounded_text(agent_id, "agent_id")
        now = self.clock()
        with self._transaction() as connection:
            self._recover_locked(connection, now)
            agent_row = connection.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if not agent_row or not agent_row["active"]:
                raise CapabilityMismatch("agent is missing or inactive")
            profile = AgentProfile.from_dict(json.loads(agent_row["profile_json"]))
            if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= profile.max_lease_seconds:
                raise LeaseConflict("lease_seconds exceeds agent limits")
            running = connection.execute(
                "SELECT COUNT(*) FROM missions WHERE status=? AND lease_owner=? AND lease_expires_at>?",
                (MissionStatus.RUNNING.value, agent_id, now),
            ).fetchone()[0]
            if running >= profile.max_running:
                raise NoMissionAvailable("agent running limit reached")
            candidates = connection.execute(
                "SELECT * FROM missions WHERE status=? ORDER BY priority DESC,created_at,mission_id LIMIT 500",
                (MissionStatus.QUEUED.value,),
            ).fetchall()
            selected = next((row for row in candidates if self._compatible(profile, row)), None)
            if selected is None:
                raise NoMissionAvailable("no compatible queued mission")
            token = self.token_factory(); expires = now + lease_seconds
            updated = connection.execute(
                """UPDATE missions SET status=?,attempts=attempts+1,lease_owner=?,lease_token=?,
                lease_expires_at=?,updated_at=?,revision=revision+1 WHERE mission_id=? AND status=?""",
                (MissionStatus.RUNNING.value, agent_id, token, expires, now, selected["mission_id"], MissionStatus.QUEUED.value),
            )
            if updated.rowcount != 1:
                raise NoMissionAvailable("mission was claimed concurrently")
            self._event(connection, selected["mission_id"], f"claim:{selected['mission_id']}:{selected['attempts'] + 1}", "claimed", agent_id, {"lease_expires_at": _iso(expires)}, now)
            row = connection.execute("SELECT * FROM missions WHERE mission_id=?", (selected["mission_id"],)).fetchone()
            return self._mission(connection, row, include_token=True)

    def heartbeat(self, mission_id: str, lease_token: str, *, lease_seconds: int = 60) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id"); lease_token = _bounded_text(lease_token, "lease_token")
        now = self.clock()
        with self._transaction() as connection:
            row = self._owned_running(connection, mission_id, lease_token, now)
            profile = AgentProfile.from_dict(json.loads(connection.execute("SELECT profile_json FROM agents WHERE agent_id=?", (row["lease_owner"],)).fetchone()[0]))
            if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= profile.max_lease_seconds:
                raise LeaseConflict("lease_seconds exceeds agent limits")
            expires = now + lease_seconds
            connection.execute("UPDATE missions SET lease_expires_at=?,updated_at=?,revision=revision+1 WHERE mission_id=?", (expires, now, mission_id))
            self._event(connection, mission_id, f"heartbeat:{mission_id}:{row['revision'] + 1}", "heartbeat", row["lease_owner"], {"lease_expires_at": _iso(expires)}, now)
            return self._mission(connection, connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone())

    def complete(self, mission_id: str, lease_token: str, evidence: CompletionEvidence) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id"); lease_token = _bounded_text(lease_token, "lease_token")
        if not evidence.sufficient:
            raise EvidenceRequired("done requires passed tests and at least one commit or artifact")
        now = self.clock()
        with self._transaction() as connection:
            row = self._owned_running(connection, mission_id, lease_token, now)
            connection.execute(
                """UPDATE missions SET status=?,evidence_json=?,evidence_sha256=?,lease_owner=NULL,
                lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE mission_id=?""",
                (MissionStatus.DONE.value, canonical_json(evidence.to_dict()), evidence.sha256, now, mission_id),
            )
            self._event(connection, mission_id, f"done:{mission_id}:{evidence.sha256}", "done", row["lease_owner"], {"evidence_sha256": evidence.sha256}, now)
            return self._mission(connection, connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone())

    def wait(self, mission_id: str, lease_token: str, reason: str) -> dict[str, object]:
        return self._finish_running(mission_id, lease_token, MissionStatus.WAITING, reason, retryable=False)

    def reject(self, mission_id: str, lease_token: str, reason: str) -> dict[str, object]:
        return self._finish_running(mission_id, lease_token, MissionStatus.REJECTED, reason, retryable=False)

    def fail(self, mission_id: str, lease_token: str, reason: str, *, retryable: bool = True) -> dict[str, object]:
        return self._finish_running(mission_id, lease_token, MissionStatus.FAILED, reason, retryable=retryable)

    def _finish_running(self, mission_id: str, token: str, target: MissionStatus, reason: str, *, retryable: bool) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id"); token = _bounded_text(token, "lease_token")
        reason = _bounded_text(reason, "reason")
        now = self.clock()
        with self._transaction() as connection:
            row = self._owned_running(connection, mission_id, token, now)
            status = MissionStatus.QUEUED if retryable and row["attempts"] < row["max_retries"] + 1 else target
            waiting = reason.strip() if status is MissionStatus.WAITING else None
            failure = reason.strip() if target is MissionStatus.FAILED else None
            connection.execute(
                """UPDATE missions SET status=?,waiting_reason=?,failure_reason=?,lease_owner=NULL,
                lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE mission_id=?""",
                (status.value, waiting, failure, now, mission_id),
            )
            self._event(connection, mission_id, f"{target.value}:{mission_id}:{row['revision'] + 1}", target.value, row["lease_owner"], {"reason": reason.strip(), "resulting_status": status.value}, now)
            return self._mission(connection, connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone())

    def retry(self, mission_id: str, *, actor: str, reason: str) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id"); actor = _bounded_text(actor, "actor"); reason = _bounded_text(reason, "reason")
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if not row: raise MissionNotFound(mission_id)
            if row["status"] not in {MissionStatus.WAITING.value, MissionStatus.FAILED.value}:
                raise StateConflict("only waiting/failed missions can be retried")
            if row["attempts"] >= row["max_retries"] + 1:
                raise StateConflict("retry budget exhausted")
            connection.execute("UPDATE missions SET status=?,waiting_reason=NULL,updated_at=?,revision=revision+1 WHERE mission_id=?", (MissionStatus.QUEUED.value, now, mission_id))
            self._event(connection, mission_id, f"retry:{mission_id}:{row['revision'] + 1}", "retry", actor, {"reason": reason}, now)
            return self._mission(connection, connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone())

    def record_signal(self, mission_id: str, *, event_id: str, kind: str, actor: str, detail: Mapping[str, Any]) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id"); event_id = _bounded_text(event_id, "event_id"); actor = _bounded_text(actor, "actor")
        if kind not in {"disagreement", "escalation"}: raise StateConflict("kind must be disagreement or escalation")
        if not isinstance(detail, Mapping): raise StateConflict("detail must be an object")
        now = self.clock()
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM missions WHERE mission_id=?", (mission_id,)).fetchone(): raise MissionNotFound(mission_id)
            self._event(connection, mission_id, event_id, kind, actor, detail, now)
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            return self._event_dict(row)

    def recover_expired(self) -> dict[str, int]:
        with self._transaction() as connection: return self._recover_locked(connection, self.clock())

    def get(self, mission_id: str) -> dict[str, object]:
        mission_id = _bounded_text(mission_id, "mission_id")
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if not row: raise MissionNotFound(mission_id)
            return self._mission(connection, row)

    def list(self, *, status: MissionStatus | None = None, owner_scope: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200: raise StateConflict("limit must be 1..200")
        self.initialize(); clauses, values = [], []
        if status: clauses.append("status=?"); values.append(status.value)
        if owner_scope: clauses.append("owner_scope=?"); values.append(owner_scope)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(f"SELECT * FROM missions{where} ORDER BY priority DESC,created_at,mission_id LIMIT ?", (*values, limit)).fetchall()
            return [self._mission(connection, row) for row in rows]

    def inventory(self) -> dict[str, object]:
        self.initialize()
        with closing(self._connect()) as connection:
            counts = {row["status"]: row["count"] for row in connection.execute("SELECT status,COUNT(*) count FROM missions GROUP BY status")}
            agents = connection.execute("SELECT COUNT(*) total,SUM(active) active FROM agents").fetchone()
            signals = {row["kind"]: row["count"] for row in connection.execute("SELECT kind,COUNT(*) count FROM events WHERE kind IN ('disagreement','escalation') GROUP BY kind")}
            body = {"schema_version": "1.0", "missions": {status.value: counts.get(status.value, 0) for status in MissionStatus}, "agents": {"total": agents["total"], "active": agents["active"] or 0}, "signals": {"disagreements": signals.get("disagreement", 0), "escalations": signals.get("escalation", 0)}}
            return {**body, "inventory_sha256": sha256_json(body)}

    def list_agents(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise StateConflict("limit must be 1..200")
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM agents ORDER BY agent_id LIMIT ?", (limit,)).fetchall()
            return [
                {
                    **json.loads(row["profile_json"]),
                    "updated_at": _iso(row["updated_at"]),
                    "profile_sha256": sha256_json(json.loads(row["profile_json"])),
                }
                for row in rows
            ]

    def _recover_locked(self, connection: sqlite3.Connection, now: float) -> dict[str, int]:
        recovered = failed = 0
        rows = connection.execute("SELECT * FROM missions WHERE status=? AND lease_expires_at<=?", (MissionStatus.RUNNING.value, now)).fetchall()
        for row in rows:
            status = MissionStatus.QUEUED if row["attempts"] < row["max_retries"] + 1 else MissionStatus.FAILED
            reason = None if status is MissionStatus.QUEUED else "lease expired and retry budget exhausted"
            connection.execute("UPDATE missions SET status=?,failure_reason=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE mission_id=?", (status.value, reason, now, row["mission_id"]))
            self._event(connection, row["mission_id"], f"lease-expired:{row['mission_id']}:{row['attempts']}", "lease_expired", "system", {"resulting_status": status.value}, now)
            if status is MissionStatus.QUEUED: recovered += 1
            else: failed += 1
        return {"recovered": recovered, "failed": failed}

    @staticmethod
    def _compatible(profile: AgentProfile, row: sqlite3.Row) -> bool:
        capabilities = set(json.loads(row["required_capabilities_json"])); permissions = set(json.loads(row["required_permissions_json"]))
        owns = "*" in profile.ownership or row["owner_scope"] in profile.ownership
        return owns and capabilities <= set(profile.capabilities) and permissions <= set(profile.permissions)

    def _owned_running(self, connection: sqlite3.Connection, mission_id: str, token: str, now: float) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
        if not row: raise MissionNotFound(mission_id)
        if row["status"] != MissionStatus.RUNNING.value: raise StateConflict("mission is not running")
        if not secrets.compare_digest(row["lease_token"] or "", token): raise LeaseConflict("lease token mismatch")
        if row["lease_expires_at"] <= now: raise LeaseConflict("lease expired")
        return row

    def _mission(self, connection: sqlite3.Connection, row: sqlite3.Row, *, include_token: bool = False) -> dict[str, object]:
        events = [self._event_dict(event) for event in connection.execute("SELECT * FROM events WHERE mission_id=? ORDER BY sequence", (row["mission_id"],))]
        value = {
            "mission_id": row["mission_id"], "idempotency_key": row["idempotency_key"],
            "title": row["title"], "priority": row["priority"], "owner_scope": row["owner_scope"],
            "status": row["status"], "attempts": row["attempts"], "max_retries": row["max_retries"],
            "lease_owner": row["lease_owner"], "lease_expires_at": _iso(row["lease_expires_at"]),
            "waiting_reason": row["waiting_reason"], "failure_reason": row["failure_reason"],
            "logical_sha256": row["logical_sha256"], "evidence_sha256": row["evidence_sha256"],
            "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else None,
            "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
            "revision": row["revision"], "events": events,
            "spec": json.loads(row["spec_json"]),
        }
        if include_token: value["lease_token"] = row["lease_token"]
        return value

    def _event(self, connection: sqlite3.Connection, mission_id: str, event_id: str, kind: str, actor: str, detail: Mapping[str, Any], now: float) -> None:
        try: detail_json = canonical_json(detail)
        except (TypeError, ValueError) as exc: raise StateConflict("event detail must be JSON serializable") from exc
        if len(detail_json.encode("utf-8")) > 100_000: raise StateConflict("event detail exceeds 100000 bytes")
        existing = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            if existing["mission_id"] != mission_id or existing["kind"] != kind or existing["actor"] != actor or existing["detail_json"] != detail_json:
                raise IdempotencyConflict("event_id already has different content")
            return
        connection.execute("INSERT INTO events(event_id,mission_id,kind,actor,detail_json,created_at) VALUES(?,?,?,?,?,?)", (event_id, mission_id, kind, actor, detail_json, now))

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, object]:
        return {"sequence": row["sequence"], "event_id": row["event_id"], "kind": row["kind"], "actor": row["actor"], "detail": json.loads(row["detail_json"]), "created_at": _iso(row["created_at"])}
