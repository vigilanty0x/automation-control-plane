"""Transactional SQLite state for task graphs."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .models import ContractError, GraphSpec, canonical_json, sha256


SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_meta (graph_id TEXT PRIMARY KEY, graph_sha TEXT NOT NULL, spec_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
  graph_id TEXT NOT NULL, task_id TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_until INTEGER, evidence_json TEXT NOT NULL DEFAULT '[]', result_json TEXT,
  last_error TEXT, PRIMARY KEY (graph_id, task_id)
);
CREATE TABLE IF NOT EXISTS events (
  graph_id TEXT NOT NULL, event_id TEXT NOT NULL, task_id TEXT NOT NULL, event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL, payload_sha TEXT NOT NULL, PRIMARY KEY (graph_id, event_id)
);
"""


class TaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def load_graph(self, graph: GraphSpec) -> bool:
        spec = canonical_json(graph.to_dict())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT graph_sha FROM graph_meta WHERE graph_id=?", (graph.graph_id,)).fetchone()
            if row:
                if row["graph_sha"] != graph.digest:
                    raise ContractError("graph ID already exists with a different contract")
                self.connection.execute("COMMIT")
                return False
            self.connection.execute("INSERT INTO graph_meta VALUES (?,?,?)", (graph.graph_id, graph.digest, spec))
            self.connection.executemany(
                "INSERT INTO tasks(graph_id,task_id,state) VALUES (?,?,?)",
                [(graph.graph_id, task.task_id, "queued") for task in graph.tasks],
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def graph(self, graph_id: str) -> GraphSpec:
        row = self.connection.execute("SELECT spec_json FROM graph_meta WHERE graph_id=?", (graph_id,)).fetchone()
        if row is None:
            raise ContractError(f"unknown graph: {graph_id}")
        return GraphSpec.from_json(row["spec_json"])

    def task_rows(self, graph_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM tasks WHERE graph_id=? ORDER BY task_id", (graph_id,)).fetchall()
        return [dict(row) for row in rows]

    def event(self, graph_id: str, event_id: str, task_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        encoded = canonical_json(payload)
        digest = sha256(payload)
        existing = self.connection.execute("SELECT payload_sha,event_type,task_id FROM events WHERE graph_id=? AND event_id=?", (graph_id, event_id)).fetchone()
        if existing:
            if existing["payload_sha"] == digest and existing["event_type"] == event_type and existing["task_id"] == task_id:
                return False
            raise ContractError("idempotency event conflict")
        self.connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (graph_id, event_id, task_id, event_type, encoded, digest))
        return True

