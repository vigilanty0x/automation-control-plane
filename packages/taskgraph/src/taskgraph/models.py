"""Versioned bounded task graph contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_KINDS = {"commit", "test", "artifact", "decision"}
STATES = {"queued", "running", "waiting", "failed", "rejected", "done"}
MAX_TASKS = 200


class ContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be an array")
    return value


def _string(value: Any, name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise ContractError(f"{name} exceeds {maximum} characters")
    return value


def _id(value: Any, name: str) -> str:
    text = _string(value, name, 64)
    if not ID_RE.fullmatch(text):
        raise ContractError(f"{name} has invalid identifier")
    return text


@dataclass(frozen=True)
class Evidence:
    kind: str
    uri: str
    sha256: str

    @classmethod
    def from_dict(cls, raw: Any) -> "Evidence":
        data = _object(raw, "evidence")
        unknown = set(data) - {"kind", "uri", "sha256"}
        if unknown:
            raise ContractError(f"evidence has unknown fields: {sorted(unknown)}")
        kind = _string(data.get("kind"), "evidence.kind", 16)
        if kind not in EVIDENCE_KINDS:
            raise ContractError(f"evidence.kind must be one of {sorted(EVIDENCE_KINDS)}")
        digest = _string(data.get("sha256"), "evidence.sha256", 64)
        if not SHA_RE.fullmatch(digest):
            raise ContractError("evidence.sha256 must be lowercase SHA-256")
        return cls(kind, _string(data.get("uri"), "evidence.uri", 500), digest)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "uri": self.uri, "sha256": self.sha256}


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    owner: str
    description: str
    path_scope: tuple[str, ...]
    dependencies: tuple[str, ...]
    max_attempts: int
    required_evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> "TaskSpec":
        data = _object(raw, "task")
        allowed = {"task_id", "owner", "description", "path_scope", "dependencies", "max_attempts", "required_evidence"}
        unknown = set(data) - allowed
        if unknown:
            raise ContractError(f"task has unknown fields: {sorted(unknown)}")
        paths = tuple(sorted({_string(item, "task.path", 300) for item in _array(data.get("path_scope"), "task.path_scope")}))
        if not paths or any(path.startswith("/") or ".." in path.split("/") for path in paths):
            raise ContractError("task.path_scope must contain safe relative paths")
        dependencies = tuple(sorted({_id(item, "task.dependency") for item in _array(data.get("dependencies", []), "task.dependencies")}))
        required = tuple(sorted({_string(item, "task.required_evidence", 16) for item in _array(data.get("required_evidence"), "task.required_evidence")}))
        if not required or any(kind not in EVIDENCE_KINDS for kind in required):
            raise ContractError("task.required_evidence must contain supported kinds")
        maximum = data.get("max_attempts")
        if not isinstance(maximum, int) or not 1 <= maximum <= 10:
            raise ContractError("task.max_attempts must be between 1 and 10")
        task_id = _id(data.get("task_id"), "task.task_id")
        if task_id in dependencies:
            raise ContractError("task cannot depend on itself")
        return cls(task_id, _id(data.get("owner"), "task.owner"), _string(data.get("description"), "task.description", 1000), paths, dependencies, maximum, required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "owner": self.owner, "description": self.description,
            "path_scope": list(self.path_scope), "dependencies": list(self.dependencies),
            "max_attempts": self.max_attempts, "required_evidence": list(self.required_evidence),
        }


@dataclass(frozen=True)
class GraphSpec:
    schema_version: str
    graph_id: str
    version: str
    tasks: tuple[TaskSpec, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> "GraphSpec":
        data = _object(raw, "graph")
        unknown = set(data) - {"schema_version", "graph_id", "version", "tasks"}
        if unknown:
            raise ContractError(f"graph has unknown fields: {sorted(unknown)}")
        if data.get("schema_version") != "1.0":
            raise ContractError("graph.schema_version must be 1.0")
        version = _string(data.get("version"), "graph.version", 32)
        if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
            raise ContractError("graph.version must be semantic x.y.z")
        task_rows = _array(data.get("tasks"), "graph.tasks")
        if not 1 <= len(task_rows) <= MAX_TASKS:
            raise ContractError(f"graph.tasks must contain 1..{MAX_TASKS} tasks")
        tasks = tuple(TaskSpec.from_dict(item) for item in task_rows)
        task_ids = [task.task_id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ContractError("task IDs must be unique")
        task_set = set(task_ids)
        unknown_dependencies = {dep for task in tasks for dep in task.dependencies if dep not in task_set}
        if unknown_dependencies:
            raise ContractError(f"unknown dependencies: {sorted(unknown_dependencies)}")
        paths: dict[str, str] = {}
        for task in tasks:
            for path in task.path_scope:
                if path in paths:
                    raise ContractError(f"path ownership conflict: {path} owned by {paths[path]} and {task.task_id}")
                paths[path] = task.task_id
        by_id = {task.task_id: task for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ContractError("graph contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in by_id[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)
        for task_id in sorted(task_set):
            visit(task_id)
        return cls("1.0", _id(data.get("graph_id"), "graph.graph_id"), version, tasks)

    @classmethod
    def from_json(cls, text: str) -> "GraphSpec":
        if len(text) > 2_000_000:
            raise ContractError("graph JSON exceeds 2 MB")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(f"invalid JSON: {exc.msg}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "graph_id": self.graph_id, "version": self.version, "tasks": [task.to_dict() for task in self.tasks]}

    @property
    def digest(self) -> str:
        return sha256(self.to_dict())

    def topological_order(self) -> tuple[str, ...]:
        by_id = {task.task_id: task for task in self.tasks}
        remaining = set(by_id)
        done: set[str] = set()
        order: list[str] = []
        while remaining:
            ready = sorted(task_id for task_id in remaining if set(by_id[task_id].dependencies) <= done)
            order.extend(ready)
            done.update(ready)
            remaining.difference_update(ready)
        return tuple(order)

