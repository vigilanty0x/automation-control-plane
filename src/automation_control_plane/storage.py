"""SQLite persistence and hash-chained audit support.

Every mutating engine operation uses ``BEGIN IMMEDIATE`` so decisions, state,
audit entries, and outbox records commit together.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator, Mapping
from urllib.parse import quote

from .models import WorkflowDefinition, canonical_json
from .policy import DEFAULT_ROLES


DATABASE_SCHEMA_VERSION = 2
ZERO_HASH = "0" * 64
JOB_STATES = ("queued", "running", "waiting_approval", "completed", "failed", "cancelled")
STEP_STATES = ("blocked", "ready", "waiting_approval", "leased", "succeeded", "failed", "cancelled", "skipped")
REQUIRED_SCHEMA: dict[str, set[str]] = {
    "meta": {"key", "value"},
    "workflows": {"workflow_id", "version", "definition_json", "digest", "active", "created_at"},
    "jobs": {
        "job_id", "workflow_id", "workflow_version", "workflow_digest", "state", "version",
        "trigger_type", "trigger_json", "payload_json", "idempotency_key", "request_digest",
        "submitted_by", "budget_limit", "budget_spent", "budget_reserved", "fence_generation",
        "deadline_at", "dry_run", "created_at", "updated_at",
    },
    "step_runs": {
        "job_id", "step_id", "state", "version", "attempts", "max_attempts", "available_at",
        "lease_owner", "lease_token", "lease_expires_at", "lease_fence_generation",
        "approval_required", "input_digest", "result_json", "result_digest", "error",
        "estimated_cost", "reserved_cost", "charged_cost", "created_at", "updated_at",
    },
    "approvals": {
        "approval_id", "job_id", "step_id", "workflow_digest", "job_version", "step_version",
        "input_digest", "status", "requested_at", "decided_by", "decided_at", "reason",
    },
    "kill_switches": {"scope", "scope_id", "enabled", "reason", "version", "updated_by", "updated_at"},
    "roles": {"role_name"},
    "role_capabilities": {"role_name", "capability"},
    "principal_roles": {"principal", "role_name"},
    "events": {
        "sequence", "event_id", "event_type", "entity_type", "entity_id", "principal",
        "occurred_at", "payload_json", "previous_hash", "event_hash",
    },
    "outbox": {
        "sequence", "event_sequence", "topic", "payload_json", "state", "attempts",
        "available_at", "lease_owner", "lease_token", "lease_expires_at", "created_at", "delivered_at",
    },
}


class StorageError(RuntimeError):
    pass


class ConflictError(StorageError):
    pass


class NotFoundError(StorageError):
    pass


def utc_timestamp(value: datetime | None = None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _absolute_no_follow(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.abspath(candidate))


def _create_private_file(path: Path) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)


class ControlPlaneStore:
    """A connection-per-operation SQLite store safe for worker threads."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).expanduser().resolve()

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = f"file:{quote(self.path.as_posix(), safe='/:')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        else:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if not readonly:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _preflight_schema_version(self) -> int | None:
        """Inspect an existing database without changing its journal or schema."""

        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        if not self.path.is_file():
            raise StorageError(f"database path is not a regular file: {self.path}")
        uri = f"file:{quote(self.path.as_posix(), safe='/:')}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                has_meta = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
                ).fetchone()
                if has_meta is None:
                    raise StorageError("existing database has no recognized schema metadata")
                row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                if row is None:
                    raise StorageError("database schema version is missing")
                try:
                    return int(row["value"])
                except (TypeError, ValueError) as exc:
                    raise StorageError("database schema version is invalid") from exc
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"database preflight failed: {exc}") from exc

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        table_rows = connection.execute("PRAGMA table_list").fetchall()
        tables = {row["name"] for row in table_rows}
        missing_tables = sorted(set(REQUIRED_SCHEMA) - tables)
        if missing_tables:
            raise StorageError(f"database schema is incomplete; missing tables: {missing_tables}")
        unexpected_tables = sorted(
            name for name in tables if not name.startswith("sqlite_") and name not in REQUIRED_SCHEMA
        )
        if unexpected_tables:
            raise StorageError(f"database schema contains unexpected tables: {unexpected_tables}")
        executable_objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view') ORDER BY type, name"
        ).fetchall()
        if executable_objects:
            names = [f"{row['type']}:{row['name']}" for row in executable_objects]
            raise StorageError(f"database schema contains unexpected executable objects: {names}")
        non_strict = sorted(
            row["name"] for row in table_rows if row["name"] in REQUIRED_SCHEMA and not row["strict"]
        )
        if non_strict:
            raise StorageError(f"database schema is not strict: {non_strict}")
        for table, expected in REQUIRED_SCHEMA.items():
            columns = {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
            missing = sorted(expected - columns)
            if missing:
                raise StorageError(f"database schema is incomplete; {table} missing columns: {missing}")
            unexpected = sorted(columns - expected)
            if unexpected:
                raise StorageError(f"database schema contains unexpected {table} columns: {unexpected}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchmany(1)
        if foreign_key_errors:
            raise StorageError("database foreign-key integrity check failed")
        required_indexes = {
            "one_active_workflow": ("workflows", ("workflow_id",), True, True),
            "jobs_state_idx": ("jobs", ("state", "deadline_at"), False, False),
            "claim_step_idx": ("step_runs", ("state", "available_at", "lease_expires_at"), False, False),
            "outbox_delivery_idx": ("outbox", ("state", "available_at"), False, False),
        }
        for name, (table, expected_columns, unique, partial) in required_indexes.items():
            entry = next(
                (row for row in connection.execute(f'PRAGMA index_list("{table}")') if row["name"] == name),
                None,
            )
            if entry is None:
                raise StorageError(f"database schema is incomplete; missing index: {name}")
            columns = tuple(
                row["name"] for row in connection.execute(f'PRAGMA index_info("{name}")').fetchall()
            )
            if columns != expected_columns or bool(entry["unique"]) != unique or bool(entry["partial"]) != partial:
                raise StorageError(f"database index definition is invalid: {name}")
        active_index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'one_active_workflow'"
        ).fetchone()
        normalized_index = " ".join((active_index["sql"] if active_index else "").lower().split())
        if normalized_index != (
            "create unique index one_active_workflow on workflows(workflow_id) where active = 1"
        ):
            raise StorageError("database partial index definition is invalid: one_active_workflow")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise StorageError(f"database quick check failed: {quick_check}")

    @staticmethod
    def _seed_policy(connection: sqlite3.Connection) -> None:
        for role, capabilities in DEFAULT_ROLES.items():
            connection.execute("INSERT OR IGNORE INTO roles(role_name) VALUES (?)", (role,))
            connection.executemany(
                "INSERT OR IGNORE INTO role_capabilities(role_name, capability) VALUES (?, ?)",
                ((role, capability) for capability in capabilities),
            )
        connection.execute("INSERT OR IGNORE INTO principal_roles(principal, role_name) VALUES ('admin', 'admin')")

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        required_v1 = set(REQUIRED_SCHEMA) - {"meta"}
        existing = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if not required_v1 <= existing:
            raise StorageError("version 1 database is incomplete and cannot be migrated safely")
        previous_hash = ZERO_HASH
        expected_sequence = 1
        succeeded_events: dict[str, str] = {}
        for event in connection.execute("SELECT * FROM events ORDER BY sequence").fetchall():
            if event["sequence"] != expected_sequence or event["previous_hash"] != previous_hash:
                raise StorageError("version 1 audit chain is not contiguous")
            try:
                payload = json.loads(event["payload_json"])
                if canonical_json(payload) != event["payload_json"]:
                    raise ValueError("noncanonical payload")
            except (json.JSONDecodeError, ValueError) as exc:
                raise StorageError("version 1 audit payload is invalid") from exc
            record = {
                "event_id": event["event_id"], "event_type": event["event_type"],
                "entity_type": event["entity_type"], "entity_id": event["entity_id"],
                "principal": event["principal"], "occurred_at": event["occurred_at"],
                "payload": payload, "previous_hash": event["previous_hash"],
            }
            expected_hash = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
            if event["event_hash"] != expected_hash:
                raise StorageError("version 1 audit chain is invalid")
            if event["event_type"] == "step.succeeded" and isinstance(payload, dict):
                output_digest = payload.get("output_digest")
                if isinstance(output_digest, str):
                    succeeded_events[event["entity_id"]] = output_digest
            previous_hash = event["event_hash"]
            expected_sequence += 1
        connection.execute("ALTER TABLE jobs ADD COLUMN budget_reserved INTEGER NOT NULL DEFAULT 0 CHECK(budget_reserved >= 0)")
        connection.execute("ALTER TABLE jobs ADD COLUMN fence_generation INTEGER NOT NULL DEFAULT 0 CHECK(fence_generation >= 0)")
        connection.execute("ALTER TABLE step_runs ADD COLUMN lease_fence_generation INTEGER")
        connection.execute("ALTER TABLE step_runs ADD COLUMN result_digest TEXT")
        connection.execute("ALTER TABLE step_runs ADD COLUMN reserved_cost INTEGER NOT NULL DEFAULT 0 CHECK(reserved_cost >= 0)")
        succeeded = connection.execute(
            "SELECT job_id, step_id, result_json FROM step_runs WHERE state = 'succeeded'"
        ).fetchall()
        for row in succeeded:
            if row["result_json"] is None:
                raise StorageError("version 1 succeeded step is missing its result")
            try:
                digest = hashlib.sha256(canonical_json(json.loads(row["result_json"])).encode("utf-8")).hexdigest()
            except (json.JSONDecodeError, ValueError) as exc:
                raise StorageError("version 1 step result is invalid") from exc
            if succeeded_events.get(f"{row['job_id']}:{row['step_id']}") != digest:
                raise StorageError("version 1 step result does not match its success event")
            connection.execute(
                "UPDATE step_runs SET result_digest = ? WHERE job_id = ? AND step_id = ?",
                (digest, row["job_id"], row["step_id"]),
            )
        count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        tail = connection.execute("SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('audit_event_count', ?)", (str(count),))
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('audit_head_hash', ?)",
            (tail["event_hash"] if tail else ZERO_HASH,),
        )
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(DATABASE_SCHEMA_VERSION),)
        )

    def initialize(self) -> None:
        version = self._preflight_schema_version()
        if version not in {None, 1, DATABASE_SCHEMA_VERSION}:
            raise StorageError(f"unsupported database schema version: {version}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if version == DATABASE_SCHEMA_VERSION:
            with self.transaction() as connection:
                self._validate_schema(connection)
                self._seed_policy(connection)
            self._secure_database_mode()
            return
        if version == 1:
            with self.transaction() as connection:
                self._migrate_v1_to_v2(connection)
                self._validate_schema(connection)
                self._seed_policy(connection)
            self._secure_database_mode()
            return
        with self._connect() as connection:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) STRICT;
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    definition_json TEXT NOT NULL,
                    digest TEXT NOT NULL CHECK(length(digest) = 64),
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workflow_id, version)
                ) STRICT;
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_workflow
                    ON workflows(workflow_id) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_version INTEGER NOT NULL,
                    workflow_digest TEXT NOT NULL CHECK(length(workflow_digest) = 64),
                    state TEXT NOT NULL CHECK(state IN {JOB_STATES}),
                    version INTEGER NOT NULL CHECK(version > 0),
                    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('manual', 'webhook', 'scheduled')),
                    trigger_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
                    submitted_by TEXT NOT NULL,
                    budget_limit INTEGER NOT NULL CHECK(budget_limit >= 0),
                    budget_spent INTEGER NOT NULL CHECK(budget_spent >= 0 AND budget_spent <= budget_limit),
                    budget_reserved INTEGER NOT NULL CHECK(budget_reserved >= 0 AND budget_spent + budget_reserved <= budget_limit),
                    fence_generation INTEGER NOT NULL CHECK(fence_generation >= 0),
                    deadline_at TEXT NOT NULL,
                    dry_run INTEGER NOT NULL CHECK(dry_run IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(workflow_id, workflow_version) REFERENCES workflows(workflow_id, version),
                    UNIQUE(workflow_id, idempotency_key)
                ) STRICT;
                CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, deadline_at);
                CREATE TABLE IF NOT EXISTS step_runs (
                    job_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN {STEP_STATES}),
                    version INTEGER NOT NULL CHECK(version > 0),
                    attempts INTEGER NOT NULL CHECK(attempts >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    lease_fence_generation INTEGER,
                    approval_required INTEGER NOT NULL CHECK(approval_required IN (0, 1)),
                    input_digest TEXT NOT NULL CHECK(length(input_digest) = 64),
                    result_json TEXT,
                    result_digest TEXT,
                    error TEXT,
                    estimated_cost INTEGER NOT NULL CHECK(estimated_cost >= 0),
                    reserved_cost INTEGER NOT NULL CHECK(reserved_cost >= 0),
                    charged_cost INTEGER NOT NULL CHECK(charged_cost >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, step_id),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                ) STRICT;
                CREATE INDEX IF NOT EXISTS claim_step_idx
                    ON step_runs(state, available_at, lease_expires_at);
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    workflow_digest TEXT NOT NULL CHECK(length(workflow_digest) = 64),
                    job_version INTEGER NOT NULL,
                    step_version INTEGER NOT NULL,
                    input_digest TEXT NOT NULL CHECK(length(input_digest) = 64),
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
                    requested_at TEXT NOT NULL,
                    decided_by TEXT,
                    decided_at TEXT,
                    reason TEXT,
                    UNIQUE(job_id, step_id),
                    FOREIGN KEY(job_id, step_id) REFERENCES step_runs(job_id, step_id) ON DELETE CASCADE
                ) STRICT;
                CREATE TABLE IF NOT EXISTS kill_switches (
                    scope TEXT NOT NULL CHECK(scope IN ('global', 'workflow')),
                    scope_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    reason TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, scope_id),
                    CHECK((scope = 'global' AND scope_id = '') OR (scope = 'workflow' AND length(scope_id) > 0))
                ) STRICT;
                CREATE TABLE IF NOT EXISTS roles (
                    role_name TEXT PRIMARY KEY
                ) STRICT;
                CREATE TABLE IF NOT EXISTS role_capabilities (
                    role_name TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    PRIMARY KEY(role_name, capability),
                    FOREIGN KEY(role_name) REFERENCES roles(role_name) ON DELETE CASCADE
                ) STRICT;
                CREATE TABLE IF NOT EXISTS principal_roles (
                    principal TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    PRIMARY KEY(principal, role_name),
                    FOREIGN KEY(role_name) REFERENCES roles(role_name) ON DELETE CASCADE
                ) STRICT;
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL CHECK(length(previous_hash) = 64),
                    event_hash TEXT NOT NULL CHECK(length(event_hash) = 64)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_sequence INTEGER NOT NULL UNIQUE,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'delivered')),
                    attempts INTEGER NOT NULL CHECK(attempts >= 0),
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    FOREIGN KEY(event_sequence) REFERENCES events(sequence)
                ) STRICT;
                CREATE INDEX IF NOT EXISTS outbox_delivery_idx ON outbox(state, available_at);
                """
            )
            existing = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if existing and int(existing["value"]) != DATABASE_SCHEMA_VERSION:
                raise StorageError("unsupported database schema version")
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(DATABASE_SCHEMA_VERSION),),
            )
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('audit_event_count', '0')")
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('audit_head_hash', ?)", (ZERO_HASH,))
            self._seed_policy(connection)
            self._validate_schema(connection)
            connection.commit()
        self._secure_database_mode()

    def _secure_database_mode(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def ensure_initialized(self) -> None:
        if not self.path.is_file():
            raise StorageError(f"database does not exist: {self.path}")
        with self._connect(readonly=True) as connection:
            try:
                row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                if row is None or int(row["value"]) != DATABASE_SCHEMA_VERSION:
                    raise StorageError("database schema is missing or unsupported")
                self._validate_schema(connection)
            except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
                if isinstance(exc, StorageError):
                    raise
                raise StorageError(f"database schema validation failed: {exc}") from exc

    def capabilities(self, principal: str, connection: sqlite3.Connection | None = None) -> set[str]:
        owned = connection is None
        db = connection or self._connect(readonly=True)
        try:
            rows = db.execute(
                """SELECT DISTINCT rc.capability
                   FROM principal_roles pr
                   JOIN role_capabilities rc ON rc.role_name = pr.role_name
                   WHERE pr.principal = ?""",
                (principal,),
            ).fetchall()
            return {row["capability"] for row in rows}
        finally:
            if owned:
                db.close()

    def append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        principal: str,
        occurred_at: str,
        payload: Mapping[str, Any],
        outbox: bool = True,
    ) -> int:
        payload_json = canonical_json(dict(payload))
        previous = connection.execute("SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = previous["event_hash"] if previous else ZERO_HASH
        anchors = {
            row["key"]: row["value"]
            for row in connection.execute(
                "SELECT key, value FROM meta WHERE key IN ('audit_event_count', 'audit_head_hash')"
            ).fetchall()
        }
        actual_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        if (
            anchors.get("audit_head_hash") != previous_hash
            or anchors.get("audit_event_count") != str(actual_count)
        ):
            raise StorageError("audit anchor does not match stored event chain")
        event_id = secrets.token_hex(16)
        record = {
            "event_id": event_id,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "principal": principal,
            "occurred_at": occurred_at,
            "payload": json.loads(payload_json),
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
        cursor = connection.execute(
            """INSERT INTO events(
                   event_id, event_type, entity_type, entity_id, principal, occurred_at,
                   payload_json, previous_hash, event_hash
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                entity_type,
                entity_id,
                principal,
                occurred_at,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )
        sequence = int(cursor.lastrowid)
        if outbox:
            envelope = canonical_json(
                {
                    "event_sequence": sequence,
                    "event_id": event_id,
                    "event_type": event_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "principal": principal,
                    "occurred_at": occurred_at,
                    "payload": json.loads(payload_json),
                    "event_hash": event_hash,
                }
            )
            connection.execute(
                """INSERT INTO outbox(
                       event_sequence, topic, payload_json, state, attempts, available_at, created_at
                   ) VALUES (?, ?, ?, 'pending', 0, ?, ?)""",
                (sequence, f"control_plane.{event_type}", envelope, occurred_at, occurred_at),
            )
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'audit_event_count'", (str(actual_count + 1),)
        )
        connection.execute("UPDATE meta SET value = ? WHERE key = 'audit_head_hash'", (event_hash,))
        return sequence

    def verify_audit(self) -> dict[str, Any]:
        self.ensure_initialized()
        with self._connect(readonly=True) as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            anchors = {
                row["key"]: row["value"]
                for row in connection.execute(
                    "SELECT key, value FROM meta WHERE key IN ('audit_event_count', 'audit_head_hash')"
                ).fetchall()
            }
            succeeded_steps = connection.execute(
                "SELECT job_id, step_id, result_json, result_digest FROM step_runs WHERE state = 'succeeded'"
            ).fetchall()
            outbox_rows = connection.execute(
                "SELECT event_sequence, topic, payload_json FROM outbox ORDER BY event_sequence"
            ).fetchall()
        previous_hash = ZERO_HASH
        errors: list[dict[str, Any]] = []
        succeeded_events: dict[str, str] = {}
        expected_sequence = 1
        for row in rows:
            if row["sequence"] != expected_sequence:
                errors.append({"sequence": row["sequence"], "reason": "sequence_gap"})
                expected_sequence = row["sequence"]
            try:
                payload = json.loads(row["payload_json"])
                canonical_payload = canonical_json(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append({"sequence": row["sequence"], "reason": "invalid_payload", "detail": str(exc)})
                previous_hash = row["event_hash"]
                expected_sequence += 1
                continue
            if canonical_payload != row["payload_json"]:
                errors.append({"sequence": row["sequence"], "reason": "noncanonical_payload"})
            record = {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "principal": row["principal"],
                "occurred_at": row["occurred_at"],
                "payload": payload,
                "previous_hash": row["previous_hash"],
            }
            expected_hash = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
            if row["previous_hash"] != previous_hash:
                errors.append({"sequence": row["sequence"], "reason": "previous_hash_mismatch"})
            if row["event_hash"] != expected_hash:
                errors.append({"sequence": row["sequence"], "reason": "event_hash_mismatch"})
            if row["event_type"] == "step.succeeded" and isinstance(payload, dict):
                output_digest = payload.get("output_digest")
                if isinstance(output_digest, str):
                    succeeded_events[row["entity_id"]] = output_digest
            previous_hash = row["event_hash"]
            expected_sequence += 1
        if anchors.get("audit_event_count") != str(len(rows)):
            errors.append({"sequence": 0, "reason": "event_count_anchor_mismatch"})
        if anchors.get("audit_head_hash") != previous_hash:
            errors.append({"sequence": 0, "reason": "head_hash_anchor_mismatch"})
        for step in succeeded_steps:
            entity_id = f"{step['job_id']}:{step['step_id']}"
            try:
                if step["result_json"] is None:
                    raise ValueError("missing result")
                result = json.loads(step["result_json"])
                canonical_result = canonical_json(result)
                digest = hashlib.sha256(canonical_result.encode("utf-8")).hexdigest()
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append({"entity_id": entity_id, "reason": "invalid_step_result", "detail": str(exc)})
                continue
            if canonical_result != step["result_json"]:
                errors.append({"entity_id": entity_id, "reason": "noncanonical_step_result"})
            if step["result_digest"] != digest:
                errors.append({"entity_id": entity_id, "reason": "result_digest_mismatch"})
            if succeeded_events.get(entity_id) != digest:
                errors.append({"entity_id": entity_id, "reason": "result_event_mismatch"})
        outbox_by_event = {row["event_sequence"]: row for row in outbox_rows}
        event_sequences = {row["sequence"] for row in rows}
        for row in rows:
            outbox = outbox_by_event.get(row["sequence"])
            if outbox is None:
                errors.append({"sequence": row["sequence"], "reason": "outbox_missing"})
                continue
            try:
                payload = json.loads(row["payload_json"])
                expected_envelope = canonical_json(
                    {
                        "event_sequence": row["sequence"],
                        "event_id": row["event_id"],
                        "event_type": row["event_type"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "principal": row["principal"],
                        "occurred_at": row["occurred_at"],
                        "payload": payload,
                        "event_hash": row["event_hash"],
                    }
                )
            except (json.JSONDecodeError, ValueError):
                continue
            if outbox["topic"] != f"control_plane.{row['event_type']}":
                errors.append({"sequence": row["sequence"], "reason": "outbox_topic_mismatch"})
            if outbox["payload_json"] != expected_envelope:
                errors.append({"sequence": row["sequence"], "reason": "outbox_payload_mismatch"})
        for orphaned in sorted(set(outbox_by_event) - event_sequences):
            errors.append({"sequence": orphaned, "reason": "outbox_orphaned"})
        return {
            "valid": not errors,
            "events": len(rows),
            "head_hash": previous_hash,
            "errors": errors,
        }

    def load_workflow(
        self, workflow_id: str, version: int | None = None, connection: sqlite3.Connection | None = None
    ) -> WorkflowDefinition:
        owned = connection is None
        db = connection or self._connect(readonly=True)
        try:
            if version is None:
                row = db.execute(
                    "SELECT definition_json FROM workflows WHERE workflow_id = ? AND active = 1", (workflow_id,)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT definition_json FROM workflows WHERE workflow_id = ? AND version = ?",
                    (workflow_id, version),
                ).fetchone()
            if row is None:
                raise NotFoundError(f"workflow not found: {workflow_id} version={version}")
            return WorkflowDefinition.from_json(row["definition_json"])
        finally:
            if owned:
                db.close()

    def get_job(self, job_id: str) -> dict[str, Any]:
        self.ensure_initialized()
        with self._connect(readonly=True) as connection:
            job = _row_dict(connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())
            if job is None:
                raise NotFoundError(f"job not found: {job_id}")
            steps = [dict(row) for row in connection.execute(
                "SELECT * FROM step_runs WHERE job_id = ? ORDER BY rowid", (job_id,)
            ).fetchall()]
            approvals = [dict(row) for row in connection.execute(
                "SELECT * FROM approvals WHERE job_id = ? ORDER BY rowid", (job_id,)
            ).fetchall()]
        for key in ("trigger_json", "payload_json"):
            job[key.removesuffix("_json")] = json.loads(job.pop(key))
        job["dry_run"] = bool(job["dry_run"])
        for step in steps:
            if step["result_json"] is not None:
                try:
                    step["result"] = json.loads(step["result_json"])
                    canonical_result = canonical_json(step["result"])
                except (json.JSONDecodeError, ValueError) as exc:
                    raise StorageError(f"stored result is invalid for {job_id}:{step['step_id']}") from exc
                digest = hashlib.sha256(canonical_result.encode("utf-8")).hexdigest()
                if canonical_result != step["result_json"] or step["result_digest"] != digest:
                    raise StorageError(f"stored result integrity check failed for {job_id}:{step['step_id']}")
                step.pop("result_json")
            else:
                step.pop("result_json")
            step["approval_required"] = bool(step["approval_required"])
            step["lease_active"] = step.pop("lease_token") is not None
        job["steps"] = steps
        job["approvals"] = approvals
        return job

    def list_jobs(self, *, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect(readonly=True) as connection:
            columns = """job_id, workflow_id, workflow_version, state, version, trigger_type,
                         submitted_by, budget_limit, budget_spent, deadline_at, dry_run,
                         created_at, updated_at"""
            if state is None:
                rows = connection.execute(
                    f"SELECT {columns} FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                if state not in JOB_STATES:
                    raise ValueError("unknown job state")
                rows = connection.execute(
                    f"SELECT {columns} FROM jobs WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, limit)
                ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["dry_run"] = bool(item["dry_run"])
        return result

    def list_workflows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                """SELECT workflow_id, version, digest, active, created_at
                   FROM workflows ORDER BY workflow_id, version DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["active"] = bool(item["active"])
        return result

    def list_events(self, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise ValueError("invalid event pagination")
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE sequence > ? ORDER BY sequence LIMIT ?", (after, limit)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def list_outbox(self, *, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if state is not None and state not in {"pending", "leased", "delivered"}:
            raise ValueError("unknown outbox state")
        with self._connect(readonly=True) as connection:
            if state is None:
                rows = connection.execute("SELECT * FROM outbox ORDER BY sequence LIMIT ?", (limit,)).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM outbox WHERE state = ? ORDER BY sequence LIMIT ?", (state, limit)
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["lease_active"] = item.pop("lease_token") is not None
            result.append(item)
        return result

    def list_kill_switches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT * FROM kill_switches ORDER BY scope, scope_id LIMIT ?", (limit,)
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["enabled"] = bool(item["enabled"])
        return result

    def backup(self, destination: str | os.PathLike[str]) -> Path:
        self.ensure_initialized()
        if not self.verify_audit()["valid"]:
            raise StorageError("refusing to back up an invalid audit/result chain")
        target = _absolute_no_follow(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise StorageError(f"backup destination already exists: {target}")
        temporary = target.with_name(f".{target.name}.backup-{secrets.token_hex(6)}")
        source = self._connect(readonly=True)
        destination_connection: sqlite3.Connection | None = None
        try:
            _create_private_file(temporary)
            destination_connection = sqlite3.connect(temporary)
            source.backup(destination_connection)
            integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise StorageError(f"backup integrity check failed: {integrity}")
            destination_connection.close()
            destination_connection = None
            copied = ControlPlaneStore(temporary)
            copied.ensure_initialized()
            if not copied.verify_audit()["valid"]:
                raise StorageError("backup copy has an invalid audit/result chain")
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise StorageError(f"backup destination appeared during creation: {target}") from exc
            temporary.unlink()
        except Exception:
            if destination_connection is not None:
                destination_connection.close()
            temporary.unlink(missing_ok=True)
            raise
        finally:
            source.close()
            try:
                if destination_connection is not None:
                    destination_connection.close()
            except Exception:
                pass
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    @classmethod
    def restore(
        cls, source: str | os.PathLike[str], destination: str | os.PathLike[str], *, force: bool = False
    ) -> "ControlPlaneStore":
        source_path = _absolute_no_follow(source)
        target = _absolute_no_follow(destination)
        if source_path.is_symlink() or not source_path.is_file():
            raise StorageError(f"restore source is not a file: {source_path}")
        if target.is_symlink():
            raise StorageError("restore destination must not be a symbolic link")
        if target.exists() and not force:
            raise StorageError("restore destination exists; pass force=True to replace it")
        sidecars = [Path(f"{target}{suffix}") for suffix in ("-wal", "-shm")]
        if target.exists() and any(path.exists() for path in sidecars):
            raise StorageError("restore destination has SQLite sidecars; stop/checkpoint it before replacement")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.restore-{secrets.token_hex(6)}")
        try:
            source_uri = f"file:{quote(source_path.as_posix(), safe='/:')}?mode=ro"
            source_connection = sqlite3.connect(source_uri, uri=True)
            try:
                integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise StorageError(f"restore source integrity check failed: {integrity}")
                _create_private_file(temporary)
                destination_connection = sqlite3.connect(temporary)
                try:
                    source_connection.backup(destination_connection)
                finally:
                    destination_connection.close()
            finally:
                source_connection.close()
            candidate = cls(temporary)
            candidate.initialize()
            candidate.ensure_initialized()
            audit = candidate.verify_audit()
            if not audit["valid"]:
                raise StorageError("restore source has an invalid audit chain")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        restored = cls(target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return restored
