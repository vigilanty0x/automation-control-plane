"""Fail-closed scheduling, retries, resume, and evidence gates."""

from __future__ import annotations

import json
from typing import Any

from .models import ContractError, Evidence, GraphSpec, STATES
from .store import TaskStore


class TaskGraphEngine:
    def __init__(self, store: TaskStore):
        self.store = store

    def register(self, graph: GraphSpec) -> bool:
        return self.store.load_graph(graph)

    def _reject_blocked(self, graph: GraphSpec) -> None:
        rows = {row["task_id"]: row for row in self.store.task_rows(graph.graph_id)}
        changed = True
        while changed:
            changed = False
            for task in graph.tasks:
                row = rows[task.task_id]
                if row["state"] not in {"queued", "waiting"}:
                    continue
                if any(rows[dep]["state"] in {"failed", "rejected"} for dep in task.dependencies):
                    self.store.connection.execute("UPDATE tasks SET state='rejected',last_error='dependency_not_done' WHERE graph_id=? AND task_id=?", (graph.graph_id, task.task_id))
                    row["state"] = "rejected"
                    changed = True

    def resume_expired(self, graph_id: str, now: int) -> int:
        graph = self.store.graph(graph_id)
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.store.task_rows(graph_id)
            by_id = {task.task_id: task for task in graph.tasks}
            resumed = 0
            for row in rows:
                if row["state"] == "running" and row["lease_until"] is not None and row["lease_until"] <= now:
                    state = "waiting" if row["attempts"] < by_id[row["task_id"]].max_attempts else "failed"
                    self.store.connection.execute("UPDATE tasks SET state=?,lease_owner=NULL,lease_until=NULL,last_error='lease_expired' WHERE graph_id=? AND task_id=?", (state, graph_id, row["task_id"]))
                    resumed += 1
            self._reject_blocked(graph)
            self.store.connection.execute("COMMIT")
            return resumed
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise

    def claim(self, graph_id: str, worker: str, now: int, lease_seconds: int = 300) -> dict[str, Any] | None:
        if not worker or len(worker) > 64 or lease_seconds < 1 or lease_seconds > 3600:
            raise ContractError("worker and lease_seconds must be bounded")
        self.resume_expired(graph_id, now)
        graph = self.store.graph(graph_id)
        by_id = {task.task_id: task for task in graph.tasks}
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self._reject_blocked(graph)
            rows = {row["task_id"]: row for row in self.store.task_rows(graph_id)}
            ready = [
                task for task in graph.tasks
                if rows[task.task_id]["state"] in {"queued", "waiting"}
                and all(rows[dep]["state"] == "done" for dep in task.dependencies)
            ]
            if not ready:
                self.store.connection.execute("COMMIT")
                return None
            task = sorted(ready, key=lambda item: item.task_id)[0]
            self.store.connection.execute(
                "UPDATE tasks SET state='running',attempts=attempts+1,lease_owner=?,lease_until=?,last_error=NULL WHERE graph_id=? AND task_id=?",
                (worker, now + lease_seconds, graph_id, task.task_id),
            )
            self.store.connection.execute("COMMIT")
            row = self.store.connection.execute("SELECT * FROM tasks WHERE graph_id=? AND task_id=?", (graph_id, task.task_id)).fetchone()
            return {"task": task.to_dict(), "runtime": dict(row)}
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise

    def complete(self, graph_id: str, task_id: str, worker: str, evidence: list[Evidence], result: dict[str, Any], event_id: str) -> bool:
        graph = self.store.graph(graph_id)
        task = next((item for item in graph.tasks if item.task_id == task_id), None)
        if task is None:
            raise ContractError(f"unknown task: {task_id}")
        evidence_kinds = {item.kind for item in evidence}
        missing = set(task.required_evidence) - evidence_kinds
        if missing:
            raise ContractError(f"missing required evidence: {sorted(missing)}")
        payload = {"evidence": [item.to_dict() for item in evidence], "result": result}
        existing = self.store.connection.execute(
            "SELECT task_id,event_type,payload_json FROM events WHERE graph_id=? AND event_id=?",
            (graph_id, event_id),
        ).fetchone()
        if existing is not None:
            if existing["task_id"] == task_id and existing["event_type"] == "complete" and json.loads(existing["payload_json"]) == payload:
                return False
            raise ContractError("idempotency event conflict")
        row = self.store.connection.execute("SELECT * FROM tasks WHERE graph_id=? AND task_id=?", (graph_id, task_id)).fetchone()
        if row is None or row["state"] != "running" or row["lease_owner"] != worker:
            raise ContractError("task is not running under this worker")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            added = self.store.event(graph_id, event_id, task_id, "complete", payload)
            if added:
                self.store.connection.execute("UPDATE tasks SET state='done',evidence_json=?,result_json=?,lease_owner=NULL,lease_until=NULL WHERE graph_id=? AND task_id=?", (json.dumps(payload["evidence"], sort_keys=True), json.dumps(result, sort_keys=True), graph_id, task_id))
            self.store.connection.execute("COMMIT")
            return added
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise

    def fail(self, graph_id: str, task_id: str, worker: str, error: str, event_id: str) -> tuple[str, bool]:
        if not error or len(error) > 1000:
            raise ContractError("error must be bounded")
        graph = self.store.graph(graph_id)
        task = next((item for item in graph.tasks if item.task_id == task_id), None)
        existing = self.store.connection.execute(
            "SELECT task_id,event_type,payload_json FROM events WHERE graph_id=? AND event_id=?",
            (graph_id, event_id),
        ).fetchone()
        if existing is not None:
            payload = json.loads(existing["payload_json"])
            if existing["task_id"] == task_id and existing["event_type"] == "fail" and payload.get("error") == error:
                return str(payload["state"]), False
            raise ContractError("idempotency event conflict")
        row = self.store.connection.execute("SELECT * FROM tasks WHERE graph_id=? AND task_id=?", (graph_id, task_id)).fetchone()
        if task is None or row is None or row["state"] != "running" or row["lease_owner"] != worker:
            raise ContractError("task is not running under this worker")
        state = "waiting" if row["attempts"] < task.max_attempts else "failed"
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            added = self.store.event(graph_id, event_id, task_id, "fail", {"error": error, "state": state})
            if added:
                self.store.connection.execute("UPDATE tasks SET state=?,last_error=?,lease_owner=NULL,lease_until=NULL WHERE graph_id=? AND task_id=?", (state, error, graph_id, task_id))
            self._reject_blocked(graph)
            self.store.connection.execute("COMMIT")
            return state, added
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise

    def snapshot(self, graph_id: str) -> dict[str, Any]:
        graph = self.store.graph(graph_id)
        rows = self.store.task_rows(graph_id)
        counts = {state: sum(1 for row in rows if row["state"] == state) for state in sorted(STATES)}
        return {
            "graph_id": graph_id, "graph_sha256": graph.digest,
            "topological_order": list(graph.topological_order()), "counts": counts,
            "tasks": rows, "success": counts["done"] == len(rows),
            "retry_count": sum(max(0, row["attempts"] - 1) for row in rows),
        }
