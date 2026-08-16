"""SQLite persistence with append-only events and verifiable hash chains."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .contracts import SPEC_VERSION, new_id, require_id, utc_now
from .errors import IntegrityError, NotFoundError, PolicyError, ValidationError
from .localfs import secure_directory, secure_regular_file
from .privacy import PrivacyGuard
from .strictjson import canonical_bytes, loads_bytes

ZERO_HASH = "0" * 64
ALLOWED_SESSION_SOURCES = frozenset({"synthetic-office/0.1.0", "jsonl-import/0.1.0", "test"})
ALLOWED_SESSION_METADATA = frozenset({"demo_id", "climate", "split", "goal", "effect", "synthetic"})
ALLOWED_AUDIT_COMPONENTS = frozenset(
    {"privacy-guard", "store", "learning", "learnpack", "api", "cli", "benchmark", "test"}
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS profiles (
  profile_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','locked','deleted'))
) STRICT;
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  mode TEXT NOT NULL CHECK(mode IN ('synthetic','import','observation')),
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('active','completed','incomplete','stopped')),
  metadata_json TEXT NOT NULL,
  event_count INTEGER,
  head_hash TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  sequence INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  privacy_class TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, sequence)
) STRICT;
CREATE INDEX IF NOT EXISTS events_profile_time ON events(profile_id, timestamp, event_id);
CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS routines (
  routine_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS questions (
  question_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  routine_id TEXT NOT NULL REFERENCES routines(routine_id),
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS answers (
  answer_id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL UNIQUE REFERENCES questions(question_id),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS memories (
  memory_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  memory_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS memory_conflicts (
  left_memory_id TEXT NOT NULL REFERENCES memories(memory_id),
  right_memory_id TEXT NOT NULL REFERENCES memories(memory_id),
  created_at TEXT NOT NULL,
  PRIMARY KEY(left_memory_id, right_memory_id)
) STRICT;
CREATE TABLE IF NOT EXISTS skills (
  skill_id TEXT NOT NULL,
  version TEXT NOT NULL,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, skill_id, version)
) STRICT;
CREATE TABLE IF NOT EXISTS imports (
  import_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
  digest TEXT NOT NULL,
  trust_state TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT,
  occurred_at TEXT NOT NULL,
  component TEXT NOT NULL,
  action TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  details_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS idempotency (
  idempotency_key TEXT PRIMARY KEY,
  route TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','completed')),
  response_status INTEGER,
  response_json TEXT,
  profile_scope_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  completed_at TEXT
) STRICT;
CREATE TRIGGER IF NOT EXISTS active_profile_sessions BEFORE INSERT ON sessions
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_episodes BEFORE INSERT ON episodes
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_routines BEFORE INSERT ON routines
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_questions BEFORE INSERT ON questions
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_memories BEFORE INSERT ON memories
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_skills BEFORE INSERT ON skills
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_imports BEFORE INSERT ON imports
WHEN NOT EXISTS (SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active')
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
CREATE TRIGGER IF NOT EXISTS active_profile_audit BEFORE INSERT ON audit_log
WHEN NEW.profile_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM profiles WHERE profile_id=NEW.profile_id AND status='active'
)
BEGIN SELECT RAISE(ABORT, 'profile is not active'); END;
"""


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ValidationError("database path must be a regular non-symlink file")
        if self.path.parent.exists() and self.path.parent.is_symlink():
            raise ValidationError("database parent must not be a symlink")
        secure_directory(self.path.parent)
        if self.path.exists():
            secure_regular_file(self.path)
            self._check_existing_schema()
        self._lock = threading.RLock()
        self.persistence_guard = PrivacyGuard()
        self.connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA busy_timeout=10000")
            self.connection.executescript(SCHEMA)
            self.connection.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (SPEC_VERSION,)
            )
            self._secure_database_files()
        except BaseException:
            self.connection.close()
            raise

    def _secure_database_files(self) -> None:
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if candidate.exists() or candidate.is_symlink():
                secure_regular_file(candidate)

    def _check_existing_schema(self) -> None:
        uri = f"file:{self.path.resolve()}?mode=ro"
        try:
            probe = sqlite3.connect(uri, uri=True, timeout=2.0)
            table = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if table is None:
                raise ValidationError("existing database has no Apprentice schema metadata")
            row = probe.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                raise ValidationError("existing database has no schema version")
            if str(row[0]) != SPEC_VERSION:
                raise ValidationError(
                    f"unsupported database schema {row[0]!r}; expected {SPEC_VERSION!r}"
                )
        except sqlite3.Error as exc:
            raise ValidationError(f"cannot validate existing database: {exc}") from exc
        finally:
            if "probe" in locals():
                probe.close()

    def close(self) -> None:
        with self._lock:
            try:
                self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self._secure_database_files()
            finally:
                self.connection.close()
            self._secure_database_files()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _encode_safe(self, payload: Any, *, max_bytes: int = 1024 * 1024) -> str:
        sanitized, _ = self.persistence_guard.sanitize_payload(payload)
        encoded = canonical_bytes(sanitized)
        if len(encoded) > max_bytes:
            raise ValidationError(f"persistence payload exceeds {max_bytes} bytes")
        return encoded.decode("utf-8")

    @staticmethod
    def _require_code(value: str, label: str, *, maximum: int = 120) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            rf"[A-Za-z0-9][A-Za-z0-9_.:/-]{{0,{maximum - 1}}}", value
        ):
            raise ValidationError(f"{label} must be a bounded identifier")
        return value

    @staticmethod
    def _require_active_profile(db: sqlite3.Connection, profile_id: str) -> None:
        row = db.execute(
            "SELECT status FROM profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("profile not found")
        if row["status"] != "active":
            raise PolicyError("profile is not active", code="PROFILE_INACTIVE")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except sqlite3.IntegrityError as exc:
                self.connection.rollback()
                raise IntegrityError(
                    "database integrity constraint rejected operation", code="STORE_CONFLICT"
                ) from exc
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()
                self._secure_database_files()

    def create_profile(self, name: str, profile_id: str | None = None) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValidationError("profile name must contain 1-120 characters")
        safe_name, _ = self.persistence_guard.scan_text(name.strip())
        identifier = profile_id or new_id("pro")
        require_id(identifier, prefix="pro")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO profiles(profile_id,name,created_at,policy_version,status) VALUES(?,?,?,?,?)",
                (identifier, safe_name, utc_now(), SPEC_VERSION, "active"),
            )
        return identifier

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT profile_id,name,created_at,policy_version,status FROM profiles ORDER BY created_at,profile_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def create_session(
        self,
        profile_id: str,
        *,
        mode: str = "synthetic",
        source: str = "synthetic-office/0.1.0",
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        require_id(profile_id, prefix="pro")
        if mode not in {"synthetic", "import", "observation"}:
            raise ValidationError("invalid session mode")
        if source not in ALLOWED_SESSION_SOURCES:
            raise ValidationError("session source is not a registered adapter identifier")
        supplied_metadata = metadata or {}
        if not isinstance(supplied_metadata, dict) or any(
            not isinstance(key, str) for key in supplied_metadata
        ):
            raise ValidationError("session metadata must be an object with string keys")
        unknown_metadata = set(supplied_metadata) - ALLOWED_SESSION_METADATA
        if unknown_metadata:
            raise ValidationError(
                f"session metadata contains unsupported fields: {', '.join(sorted(unknown_metadata))}"
            )
        for key, value in supplied_metadata.items():
            if key == "synthetic":
                if type(value) is not bool:
                    raise ValidationError("session metadata synthetic must be boolean")
            elif not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}", value
            ):
                raise ValidationError(f"session metadata {key} must be a bounded identifier")
        identifier = session_id or new_id("ses")
        require_id(identifier, prefix="ses")
        encoded = self._encode_safe(supplied_metadata, max_bytes=128 * 1024)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValidationError("session metadata exceeds 128 KiB")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            db.execute(
                "INSERT INTO sessions(session_id,profile_id,mode,source,started_at,status,metadata_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (identifier, profile_id, mode, source, utc_now(), "active", encoded),
            )
            db.execute(
                "UPDATE sessions SET event_count=0,head_hash=? WHERE session_id=?",
                (ZERO_HASH, identifier),
            )
        return identifier

    def end_session(self, profile_id: str, session_id: str, *, status: str = "completed") -> None:
        if status not in {"completed", "incomplete", "stopped"}:
            raise ValidationError("invalid final session status")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            session = db.execute(
                "SELECT status FROM sessions WHERE profile_id=? AND session_id=?",
                (profile_id, session_id),
            ).fetchone()
            if session is None or session["status"] != "active":
                raise NotFoundError("active session not found")
            anchor = db.execute(
                "SELECT COUNT(*) AS event_count,COALESCE((SELECT event_hash FROM events "
                "WHERE session_id=? ORDER BY sequence DESC LIMIT 1),?) AS head_hash "
                "FROM events WHERE session_id=?",
                (session_id, ZERO_HASH, session_id),
            ).fetchone()
            if status == "completed" and int(anchor["event_count"]) == 0:
                raise IntegrityError("a completed session must contain at least one event")
            changed = db.execute(
                "UPDATE sessions SET ended_at=?,status=?,event_count=?,head_hash=? "
                "WHERE profile_id=? AND session_id=? AND status='active'",
                (
                    utc_now(),
                    status,
                    int(anchor["event_count"]),
                    str(anchor["head_hash"]),
                    profile_id,
                    session_id,
                ),
            ).rowcount
            if changed != 1:
                raise NotFoundError("active session not found")

    def list_sessions(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM sessions WHERE profile_id=? ORDER BY started_at,session_id", (profile_id,)
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "profile_id": row["profile_id"],
                "mode": row["mode"],
                "source": row["source"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "metadata": loads_bytes(row["metadata_json"].encode()),
            }
            for row in rows
        ]

    def append_event(
        self,
        profile_id: str,
        session_id: str,
        raw_event: dict[str, Any],
        guard: PrivacyGuard,
    ) -> dict[str, Any] | None:
        decision = guard.sanitize_event(raw_event)
        if not decision.allowed:
            self.record_audit(
                profile_id,
                component="privacy-guard",
                action="event_blocked",
                reason_code=decision.reason_code,
                details={"session_id": session_id},
            )
            return None
        assert decision.event is not None
        timestamp = decision.event.get("timestamp") or utc_now()
        if not isinstance(timestamp, str) or len(timestamp) > 64 or not timestamp.endswith("Z"):
            raise ValidationError("invalid event timestamp")
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp[:-1] + "+00:00")
        except ValueError as exc:
            raise ValidationError("invalid event timestamp") from exc
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != UTC.utcoffset(parsed_timestamp):
            raise ValidationError("event timestamp must be UTC")
        event_id = decision.event.get("event_id") or new_id("evt")
        require_id(event_id, prefix="evt")
        core = dict(decision.event)
        core["event_id"] = event_id
        core["timestamp"] = timestamp
        core["session_id"] = session_id
        core.pop("integrity", None)
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            session = db.execute(
                "SELECT profile_id,status FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if session is None or session["profile_id"] != profile_id:
                raise NotFoundError("session not found for profile")
            if session["status"] != "active":
                raise IntegrityError("cannot append to a closed session")
            last = db.execute(
                "SELECT sequence,event_hash FROM events WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            sequence = 1 if last is None else int(last["sequence"]) + 1
            previous_hash = ZERO_HASH if last is None else str(last["event_hash"])
            core["sequence"] = sequence
            core_bytes = canonical_bytes(core)
            if len(core_bytes) > 1024 * 1024:
                raise ValidationError("event exceeds 1 MiB after privacy filtering")
            event_hash = hashlib.sha256(previous_hash.encode("ascii") + core_bytes).hexdigest()
            envelope = dict(core)
            envelope["integrity"] = {
                "previous_event_hash": previous_hash,
                "event_hash": event_hash,
            }
            db.execute(
                "INSERT INTO events(event_id,profile_id,session_id,sequence,timestamp,payload_json,"
                "privacy_class,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    profile_id,
                    session_id,
                    sequence,
                    timestamp,
                    self._encode_safe(envelope),
                    decision.privacy_class,
                    previous_hash,
                    event_hash,
                    utc_now(),
                ),
            )
            db.execute(
                "UPDATE sessions SET event_count=?,head_hash=? WHERE session_id=? AND status='active'",
                (sequence, event_hash, session_id),
            )
        return envelope

    def list_events(
        self,
        profile_id: str,
        *,
        session_id: str | None = None,
        limit: int = 10_000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise ValidationError("event limit must be between 1 and 10000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("event offset must be a non-negative integer")
        parameters: list[Any] = [profile_id]
        condition = "profile_id=?"
        ordering = "timestamp,session_id,sequence"
        if session_id is not None:
            condition += " AND session_id=?"
            parameters.append(session_id)
            ordering = "sequence"
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"SELECT payload_json FROM events WHERE {condition} ORDER BY {ordering} LIMIT ? OFFSET ?",
            parameters,
        ).fetchall()
        return [loads_bytes(row["payload_json"].encode("utf-8")) for row in rows]

    def verify_chain(self, profile_id: str, session_id: str) -> dict[str, Any]:
        session = self.connection.execute(
            "SELECT status,event_count,head_hash FROM sessions WHERE profile_id=? AND session_id=?",
            (profile_id, session_id),
        ).fetchone()
        if session is None:
            raise NotFoundError("session not found")
        rows = self.connection.execute(
            "SELECT sequence,payload_json,previous_hash,event_hash FROM events "
            "WHERE session_id=? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        previous = ZERO_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            if row["sequence"] != expected_sequence or row["previous_hash"] != previous:
                raise IntegrityError(f"event chain link failed at sequence {expected_sequence}")
            envelope = loads_bytes(row["payload_json"].encode("utf-8"))
            integrity = envelope.pop("integrity", None)
            calculated = hashlib.sha256(previous.encode("ascii") + canonical_bytes(envelope)).hexdigest()
            if calculated != row["event_hash"]:
                raise IntegrityError(f"event digest failed at sequence {expected_sequence}")
            if integrity != {"previous_event_hash": previous, "event_hash": calculated}:
                raise IntegrityError(f"event envelope integrity failed at sequence {expected_sequence}")
            previous = calculated
        if session["event_count"] is None or session["head_hash"] is None:
            raise IntegrityError("session has no integrity anchor")
        if int(session["event_count"]) != len(rows) or str(session["head_hash"]) != previous:
            raise IntegrityError("session tail does not match its integrity anchor")
        return {
            "session_id": session_id,
            "events": len(rows),
            "valid": True,
            "head": previous,
            "anchored": True,
            "sealed": session["status"] != "active",
            "status": session["status"],
        }

    def put_episode(self, profile_id: str, session_id: str, payload: dict[str, Any]) -> str:
        identifier = str(payload.get("episode_id") or new_id("epi"))
        require_id(identifier, prefix="epi")
        normalized = dict(payload)
        normalized["episode_id"] = identifier
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            session = db.execute(
                "SELECT 1 FROM sessions WHERE profile_id=? AND session_id=?", (profile_id, session_id)
            ).fetchone()
            if session is None:
                raise NotFoundError("session not found for episode profile")
            db.execute(
                "INSERT INTO episodes(episode_id,profile_id,session_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (identifier, profile_id, session_id, self._encode_safe(normalized), utc_now()),
            )
        return identifier

    def list_episodes(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM episodes WHERE profile_id=? ORDER BY created_at,episode_id",
            (profile_id,),
        ).fetchall()
        return [loads_bytes(row[0].encode()) for row in rows]

    def get_episode(self, profile_id: str, episode_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM episodes WHERE profile_id=? AND episode_id=?",
            (profile_id, episode_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("episode not found")
        return loads_bytes(row[0].encode())

    def put_routine(self, profile_id: str, payload: dict[str, Any]) -> str:
        identifier = str(payload.get("routine_id") or new_id("rou"))
        require_id(identifier, prefix="rou")
        normalized = dict(payload)
        normalized["routine_id"] = identifier
        status = str(normalized.get("status", "observed"))
        if status not in {"observed", "explained", "confirmed", "compilable", "rejected", "stale"}:
            raise ValidationError("invalid routine status")
        now = utc_now()
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            db.execute(
                "INSERT INTO routines(routine_id,profile_id,status,payload_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    identifier,
                    profile_id,
                    status,
                    self._encode_safe(normalized),
                    now,
                    now,
                ),
            )
        return identifier

    def get_routine(self, profile_id: str, routine_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM routines WHERE profile_id=? AND routine_id=?",
            (profile_id, routine_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("routine not found")
        return loads_bytes(row[0].encode())

    def list_routines(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM routines WHERE profile_id=? ORDER BY created_at,routine_id",
            (profile_id,),
        ).fetchall()
        return [loads_bytes(row[0].encode()) for row in rows]

    def update_routine(self, profile_id: str, routine_id: str, payload: dict[str, Any]) -> None:
        normalized = dict(payload)
        normalized["routine_id"] = routine_id
        status = str(normalized.get("status", "observed"))
        if status not in {"observed", "explained", "confirmed", "compilable", "rejected", "stale"}:
            raise ValidationError("invalid routine status")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            changed = db.execute(
                "UPDATE routines SET status=?,payload_json=?,updated_at=? WHERE profile_id=? AND routine_id=?",
                (
                    status,
                    self._encode_safe(normalized),
                    utc_now(),
                    profile_id,
                    routine_id,
                ),
            ).rowcount
            if changed != 1:
                raise NotFoundError("routine not found")

    def put_question(self, profile_id: str, routine_id: str, payload: dict[str, Any]) -> str:
        identifier = str(payload.get("id") or new_id("qst"))
        require_id(identifier, prefix="qst")
        normalized = dict(payload)
        normalized["id"] = identifier
        normalized["routine_id"] = routine_id
        status = str(normalized.get("status", "queued"))
        if status not in {"candidate", "queued", "shown", "snoozed", "answered", "dismissed", "expired"}:
            raise ValidationError("invalid question status")
        now = utc_now()
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            routine = db.execute(
                "SELECT 1 FROM routines WHERE profile_id=? AND routine_id=?", (profile_id, routine_id)
            ).fetchone()
            if routine is None:
                raise NotFoundError("routine not found for question profile")
            db.execute(
                "INSERT INTO questions(question_id,profile_id,routine_id,status,payload_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    profile_id,
                    routine_id,
                    status,
                    self._encode_safe(normalized),
                    now,
                    now,
                ),
            )
        return identifier

    def list_questions(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM questions WHERE profile_id=? ORDER BY created_at,question_id",
            (profile_id,),
        ).fetchall()
        return [loads_bytes(row[0].encode()) for row in rows]

    def get_question(self, profile_id: str, question_id: str) -> tuple[str, dict[str, Any]]:
        row = self.connection.execute(
            "SELECT routine_id,payload_json FROM questions WHERE profile_id=? AND question_id=?",
            (profile_id, question_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("question not found")
        return str(row["routine_id"]), loads_bytes(row["payload_json"].encode())

    def transition_question(
        self,
        profile_id: str,
        question_id: str,
        target: str,
        *,
        snoozed_until: str | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "queued": {"shown", "snoozed", "dismissed", "expired"},
            "shown": {"snoozed", "dismissed", "expired"},
            "snoozed": {"queued", "dismissed", "expired"},
        }
        if target not in {"queued", "shown", "snoozed", "dismissed", "expired"}:
            raise ValidationError("invalid question transition target")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            row = db.execute(
                "SELECT status,payload_json FROM questions WHERE profile_id=? AND question_id=?",
                (profile_id, question_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("question not found")
            current = str(row["status"])
            if target not in allowed.get(current, set()):
                raise IntegrityError(f"invalid question transition {current}->{target}")
            question = loads_bytes(row["payload_json"].encode())
            question["status"] = target
            if target == "snoozed":
                if not isinstance(snoozed_until, str) or not snoozed_until.endswith("Z"):
                    raise ValidationError("snoozed_until must be an explicit UTC timestamp")
                try:
                    until = datetime.fromisoformat(snoozed_until[:-1] + "+00:00")
                except ValueError as exc:
                    raise ValidationError("invalid snoozed_until timestamp") from exc
                if until <= datetime.now(UTC):
                    raise ValidationError("snoozed_until must be in the future")
                question["snoozed_until"] = snoozed_until
            else:
                question.pop("snoozed_until", None)
            db.execute(
                "UPDATE questions SET status=?,payload_json=?,updated_at=? "
                "WHERE profile_id=? AND question_id=?",
                (target, self._encode_safe(question), utc_now(), profile_id, question_id),
            )
        return question

    def commit_answer_outcome(
        self,
        profile_id: str,
        question_id: str,
        answer: dict[str, Any],
        routine: dict[str, Any],
        memory: dict[str, Any] | None,
    ) -> tuple[str, str | None]:
        answer_id = str(answer.get("answer_id", ""))
        require_id(answer_id, prefix="ans")
        normalized = dict(answer)
        normalized["question_id"] = question_id
        normalized["answered_at"] = normalized.get("answered_at") or utc_now()
        memory_id: str | None = None
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            row = db.execute(
                "SELECT routine_id,payload_json,status FROM questions WHERE profile_id=? AND question_id=?",
                (profile_id, question_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("question not found")
            if row["status"] in {"answered", "dismissed", "expired"}:
                raise IntegrityError("question is already closed")
            if str(row["routine_id"]) != str(routine.get("routine_id")):
                raise IntegrityError("answer outcome routine does not match question")
            question = loads_bytes(row["payload_json"].encode())
            question["status"] = "answered"
            db.execute(
                "INSERT INTO answers(answer_id,question_id,payload_json,created_at) VALUES(?,?,?,?)",
                (answer_id, question_id, self._encode_safe(normalized), utc_now()),
            )
            db.execute(
                "UPDATE questions SET status='answered',payload_json=?,updated_at=? WHERE question_id=?",
                (self._encode_safe(question), utc_now(), question_id),
            )
            updated = db.execute(
                "UPDATE routines SET status=?,payload_json=?,updated_at=? "
                "WHERE profile_id=? AND routine_id=?",
                (
                    str(routine.get("status", "explained")),
                    self._encode_safe(routine),
                    utc_now(),
                    profile_id,
                    row["routine_id"],
                ),
            ).rowcount
            if updated != 1:
                raise NotFoundError("routine not found during answer commit")
            if memory is not None:
                memory_id = str(memory.get("id", ""))
                require_id(memory_id, prefix="mem")
                version = memory.get("version")
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise ValidationError("memory assertions require an explicit positive integer version")
                memory_type = str(memory.get("type", "procedural"))
                if memory_type not in {"episodic", "semantic", "procedural", "policy", "negative", "provenance"}:
                    raise ValidationError("invalid memory type")
                db.execute(
                    "INSERT INTO memories(memory_id,profile_id,memory_type,status,payload_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        profile_id,
                        memory_type,
                        str(memory.get("status", "candidate")),
                        self._encode_safe(memory),
                        utc_now(),
                        utc_now(),
                    ),
                )
        return answer_id, memory_id

    def get_answer(self, profile_id: str, question_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT a.payload_json FROM answers a JOIN questions q ON q.question_id=a.question_id "
            "WHERE q.profile_id=? AND q.question_id=?",
            (profile_id, question_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("answer not found")
        return loads_bytes(row[0].encode())

    def put_memory(self, profile_id: str, payload: dict[str, Any]) -> str:
        identifier = str(payload.get("id") or new_id("mem"))
        require_id(identifier, prefix="mem")
        normalized = dict(payload)
        normalized["id"] = identifier
        version = normalized.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValidationError("memory assertions require an explicit positive integer version")
        now = utc_now()
        memory_type = str(normalized.get("type", "procedural"))
        if memory_type not in {"episodic", "semantic", "procedural", "policy", "negative", "provenance"}:
            raise ValidationError("invalid memory type")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            existing = db.execute(
                "SELECT memory_id,payload_json FROM memories WHERE profile_id=? AND memory_type=?",
                (profile_id, memory_type),
            ).fetchall()
            db.execute(
                "INSERT INTO memories(memory_id,profile_id,memory_type,status,payload_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    profile_id,
                    memory_type,
                    str(normalized.get("status", "confirmed")),
                    self._encode_safe(normalized),
                    now,
                    now,
                ),
            )
            for row in existing:
                prior = loads_bytes(row["payload_json"].encode())
                if (
                    prior.get("subject") == normalized.get("subject")
                    and prior.get("predicate") == normalized.get("predicate")
                    and prior.get("object") != normalized.get("object")
                ):
                    left, right = sorted((str(row["memory_id"]), identifier))
                    db.execute(
                        "INSERT OR IGNORE INTO memory_conflicts(left_memory_id,right_memory_id,created_at) "
                        "VALUES(?,?,?)",
                        (left, right, now),
                    )
        return identifier

    def list_memories(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM memories WHERE profile_id=? ORDER BY created_at,memory_id",
            (profile_id,),
        ).fetchall()
        return [loads_bytes(row[0].encode()) for row in rows]

    def get_memory(self, profile_id: str, memory_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM memories WHERE profile_id=? AND memory_id=?",
            (profile_id, memory_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("memory not found")
        memory = loads_bytes(row[0].encode())
        conflicts = self.connection.execute(
            "SELECT left_memory_id,right_memory_id FROM memory_conflicts "
            "WHERE left_memory_id=? OR right_memory_id=?",
            (memory_id, memory_id),
        ).fetchall()
        memory["conflicts"] = [
            row["right_memory_id"] if row["left_memory_id"] == memory_id else row["left_memory_id"]
            for row in conflicts
        ]
        return memory

    def invalidate_by_evidence(self, profile_id: str, evidence_ref: str) -> int:
        changed = 0
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            rows = db.execute(
                "SELECT memory_id,payload_json FROM memories WHERE profile_id=? AND status!='invalidated'",
                (profile_id,),
            ).fetchall()
            for row in rows:
                payload = loads_bytes(row["payload_json"].encode())
                refs = payload.get("provenance", {}).get("evidence", [])
                if evidence_ref in refs:
                    payload["status"] = "invalidated"
                    payload["invalidation_reason"] = "SOURCE_EVIDENCE_REMOVED"
                    db.execute(
                        "UPDATE memories SET status='invalidated',payload_json=?,updated_at=? WHERE memory_id=?",
                        (self._encode_safe(payload), utc_now(), row["memory_id"]),
                    )
                    changed += 1
                    answer_id = payload.get("provenance", {}).get("answer")
                    if not isinstance(answer_id, str):
                        continue
                    linked = db.execute(
                        "SELECT q.routine_id FROM answers a JOIN questions q ON q.question_id=a.question_id "
                        "WHERE q.profile_id=? AND a.answer_id=?",
                        (profile_id, answer_id),
                    ).fetchone()
                    if linked is None:
                        continue
                    routine_id = str(linked["routine_id"])
                    routine_row = db.execute(
                        "SELECT payload_json FROM routines WHERE profile_id=? AND routine_id=?",
                        (profile_id, routine_id),
                    ).fetchone()
                    if routine_row is not None:
                        routine = loads_bytes(routine_row["payload_json"].encode())
                        routine["status"] = "stale"
                        routine["invalidation_reason"] = "SOURCE_EVIDENCE_REMOVED"
                        routine["invalidated_evidence_ref"] = evidence_ref
                        db.execute(
                            "UPDATE routines SET status='stale',payload_json=?,updated_at=? "
                            "WHERE profile_id=? AND routine_id=?",
                            (self._encode_safe(routine), utc_now(), profile_id, routine_id),
                        )
                    skill_rows = db.execute(
                        "SELECT skill_id,version,payload_json FROM skills WHERE profile_id=?",
                        (profile_id,),
                    ).fetchall()
                    for skill_row in skill_rows:
                        skill = loads_bytes(skill_row["payload_json"].encode())
                        if skill.get("provenance", {}).get("routine_id") != routine_id:
                            continue
                        skill["lifecycle"] = {
                            "status": "stale",
                            "reason": "SOURCE_EVIDENCE_REMOVED",
                            "evidence_ref": evidence_ref,
                            "invalidated_at": utc_now(),
                        }
                        db.execute(
                            "UPDATE skills SET payload_json=? WHERE profile_id=? AND skill_id=? AND version=?",
                            (
                                self._encode_safe(skill),
                                profile_id,
                                skill_row["skill_id"],
                                skill_row["version"],
                            ),
                        )
        return changed

    def put_skill(self, profile_id: str, skill: dict[str, Any]) -> None:
        skill_id = str(skill.get("skill_id", ""))
        version = str(skill.get("version", ""))
        if not skill_id or not version:
            raise ValidationError("skill_id and version are required")
        self._require_code(skill_id, "skill_id", maximum=160)
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
            raise ValidationError("skill version must be semantic x.y.z")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            db.execute(
                "INSERT INTO skills(skill_id,version,profile_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (skill_id, version, profile_id, self._encode_safe(skill), utc_now()),
            )

    def list_skills(self, profile_id: str, *, include_stale: bool = False) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT payload_json FROM skills WHERE profile_id=? ORDER BY skill_id,version",
            (profile_id,),
        ).fetchall()
        skills = [loads_bytes(row[0].encode()) for row in rows]
        if include_stale:
            return skills
        return [item for item in skills if item.get("lifecycle", {}).get("status") == "active"]

    def get_skill(
        self,
        profile_id: str,
        skill_id: str,
        version: str,
        *,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM skills WHERE profile_id=? AND skill_id=? AND version=?",
            (profile_id, skill_id, version),
        ).fetchone()
        if row is None:
            raise NotFoundError("skill not found")
        skill = loads_bytes(row[0].encode())
        if not allow_stale and skill.get("lifecycle", {}).get("status") != "active":
            raise IntegrityError("skill is stale and cannot be used", code="STALE_SKILL")
        return skill

    def put_import(self, profile_id: str, digest: str, payload: dict[str, Any]) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValidationError("import digest must be a SHA-256 identifier")
        identifier = new_id("imp")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            db.execute(
                "INSERT INTO imports(import_id,profile_id,digest,trust_state,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    identifier,
                    profile_id,
                    digest,
                    "disabled_untrusted",
                    self._encode_safe(payload),
                    utc_now(),
                ),
            )
        return identifier

    def get_import(self, profile_id: str, import_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload_json FROM imports WHERE profile_id=? AND import_id=?",
            (profile_id, import_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("LearnPack import not found")
        payload = loads_bytes(row[0].encode())
        payload["import_id"] = import_id
        return payload

    def list_imports(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT import_id,payload_json FROM imports WHERE profile_id=? ORDER BY created_at,import_id",
            (profile_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = loads_bytes(row["payload_json"].encode())
            payload["import_id"] = row["import_id"]
            result.append(payload)
        return result

    def record_audit(
        self,
        profile_id: str | None,
        *,
        component: str,
        action: str,
        reason_code: str,
        details: dict[str, Any],
    ) -> None:
        self._require_code(component, "audit component", maximum=80)
        if component not in ALLOWED_AUDIT_COMPONENTS:
            raise ValidationError("audit component is not registered")
        self._require_code(action, "audit action", maximum=80)
        self._require_code(reason_code, "audit reason code", maximum=80)
        _, action_findings = self.persistence_guard.scan_text(action)
        _, reason_findings = self.persistence_guard.scan_text(reason_code)
        if action_findings or reason_findings:
            raise ValidationError("audit action/reason must never contain sensitive material")
        with self.transaction() as db:
            if profile_id is not None:
                self._require_active_profile(db, profile_id)
            db.execute(
                "INSERT INTO audit_log(profile_id,occurred_at,component,action,reason_code,details_json) "
                "VALUES(?,?,?,?,?,?)",
                (
                    profile_id,
                    utc_now(),
                    component,
                    action,
                    reason_code,
                    self._encode_safe(details, max_bytes=128 * 1024),
                ),
            )

    def audit_events(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        if profile_id is None:
            rows = self.connection.execute("SELECT * FROM audit_log ORDER BY audit_id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM audit_log WHERE profile_id=? ORDER BY audit_id", (profile_id,)
            ).fetchall()
        return [
            {
                "audit_id": row["audit_id"],
                "profile_id": row["profile_id"],
                "occurred_at": row["occurred_at"],
                "component": row["component"],
                "action": row["action"],
                "reason_code": row["reason_code"],
                "details": loads_bytes(row["details_json"].encode()),
            }
            for row in rows
        ]

    def reserve_idempotency(
        self, key: str, route: str, request_digest: str
    ) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", key):
            raise ValidationError("Idempotency-Key must be a bounded identifier")
        if not re.fullmatch(r"/[A-Za-z0-9._/-]{1,511}", route):
            raise ValidationError("idempotency route is invalid")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_digest):
            raise ValidationError("idempotency request digest is invalid")
        _, findings = self.persistence_guard.scan_text(key)
        if findings:
            raise ValidationError("Idempotency-Key must not contain sensitive material")
        with self.transaction() as db:
            row = db.execute(
                "SELECT route,request_digest,state,response_status,response_json "
                "FROM idempotency WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                if row["route"] != route or row["request_digest"] != request_digest:
                    raise IntegrityError(
                        "Idempotency-Key was already used for another request",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                if row["state"] != "completed":
                    raise IntegrityError(
                        "prior request is pending or its outcome is unknown",
                        code="IDEMPOTENCY_PENDING",
                    )
                return {
                    "status": int(row["response_status"]),
                    "response": loads_bytes(row["response_json"].encode()),
                }
            db.execute(
                "INSERT INTO idempotency(idempotency_key,route,request_digest,state,created_at) "
                "VALUES(?,?,?,'pending',?)",
                (key, route, request_digest, utc_now()),
            )
        return None

    def complete_idempotency(
        self,
        key: str,
        route: str,
        request_digest: str,
        status: int,
        response: Any,
        profile_ids: list[str],
    ) -> Any:
        if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 499:
            raise ValidationError("only non-server responses can complete idempotency")
        if (
            not isinstance(profile_ids, list)
            or len(profile_ids) > 100
            or any(not isinstance(item, str) for item in profile_ids)
        ):
            raise ValidationError("idempotency profile scope is invalid")
        normalized_profiles = sorted(set(profile_ids))
        for profile_id in normalized_profiles:
            require_id(profile_id, prefix="pro")
        sanitized_response, _ = self.persistence_guard.sanitize_payload(response)
        encoded = canonical_bytes(sanitized_response)
        if len(encoded) > 1024 * 1024:
            raise ValidationError("idempotency response exceeds 1 MiB")
        encoded_text = encoded.decode()
        encoded_scope = canonical_bytes(normalized_profiles).decode()
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE idempotency SET state='completed',response_status=?,response_json=?,"
                "profile_scope_json=?,completed_at=? "
                "WHERE idempotency_key=? AND route=? AND request_digest=? AND state='pending'",
                (status, encoded_text, encoded_scope, utc_now(), key, route, request_digest),
            ).rowcount
            if changed != 1:
                raise IntegrityError("idempotency reservation was lost", code="IDEMPOTENCY_LOST")
        return sanitized_response

    def purge_profile_data(self, profile_id: str, *, confirmation: str) -> dict[str, Any]:
        if confirmation != profile_id:
            raise ValidationError("profile purge requires exact profile id confirmation")
        with self.transaction() as db:
            self._require_active_profile(db, profile_id)
            counts: dict[str, int] = {}
            question_ids = [
                row[0]
                for row in db.execute(
                    "SELECT question_id FROM questions WHERE profile_id=?", (profile_id,)
                ).fetchall()
            ]
            memory_ids = [
                row[0]
                for row in db.execute(
                    "SELECT memory_id FROM memories WHERE profile_id=?", (profile_id,)
                ).fetchall()
            ]
            idempotency_rows = db.execute(
                "SELECT idempotency_key,profile_scope_json FROM idempotency"
            ).fetchall()
            counts["idempotency"] = 0
            for row in idempotency_rows:
                scope = loads_bytes(row["profile_scope_json"].encode())
                if profile_id in scope:
                    counts["idempotency"] += db.execute(
                        "DELETE FROM idempotency WHERE idempotency_key=?",
                        (row["idempotency_key"],),
                    ).rowcount
            for memory_id in memory_ids:
                db.execute(
                    "DELETE FROM memory_conflicts WHERE left_memory_id=? OR right_memory_id=?",
                    (memory_id, memory_id),
                )
            for question_id in question_ids:
                counts["answers"] = counts.get("answers", 0) + db.execute(
                    "DELETE FROM answers WHERE question_id=?", (question_id,)
                ).rowcount
            for table in (
                "imports",
                "skills",
                "memories",
                "questions",
                "routines",
                "episodes",
                "events",
                "sessions",
                "audit_log",
            ):
                counts[table] = db.execute(
                    f"DELETE FROM {table} WHERE profile_id=?", (profile_id,)
                ).rowcount
            counts["profiles_tombstoned"] = db.execute(
                "UPDATE profiles SET name='[deleted]',status='deleted' WHERE profile_id=?",
                (profile_id,),
            ).rowcount
        with self._lock:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.connection.execute("VACUUM")
        return {
            "profile_id": profile_id,
            "purged": counts,
            "complete": True,
            "scope": "all_profile_content_deleted; non-sensitive referential tombstone retained",
        }
