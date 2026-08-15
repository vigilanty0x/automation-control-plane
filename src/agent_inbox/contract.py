"""Bounded public contracts for missions, workers, and completion evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "1.0"
MAX_TEXT = 4_096
MAX_PAYLOAD_BYTES = 100_000
MAX_ITEMS = 100


class ContractError(ValueError):
    pass


class MissionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    REJECTED = "rejected"
    DONE = "done"


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be a string")
    value = value.strip()
    if not value and not allow_empty:
        raise ContractError(f"{name} must not be empty")
    if len(value) > MAX_TEXT:
        raise ContractError(f"{name} exceeds {MAX_TEXT} characters")
    return value


def _names(values: Sequence[object], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > MAX_ITEMS:
        raise ContractError(f"{name} must contain at most {MAX_ITEMS} items")
    items = tuple(sorted(_text(value, name) for value in values))
    if len(items) != len(set(items)):
        raise ContractError(f"{name} must be unique")
    return items


def _sha(value: object, name: str, lengths: tuple[int, ...]) -> str:
    value = _text(value, name).lower()
    if len(value) not in lengths or any(char not in "0123456789abcdef" for char in value):
        raise ContractError(f"{name} must be hexadecimal with length {lengths}")
    return value


@dataclass(frozen=True, slots=True)
class MissionSpec:
    idempotency_key: str
    title: str
    payload: Mapping[str, Any]
    priority: int = 50
    owner_scope: str = "default"
    required_capabilities: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    max_retries: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "owner_scope", _text(self.owner_scope, "owner_scope"))
        if not isinstance(self.payload, Mapping):
            raise ContractError("payload must be an object")
        try:
            encoded = canonical_json(self.payload).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContractError("payload must be JSON serializable") from exc
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ContractError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        object.__setattr__(self, "payload", json.loads(encoded))
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not 0 <= self.priority <= 100:
            raise ContractError("priority must be an integer from 0 to 100")
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool) or not 0 <= self.max_retries <= 20:
            raise ContractError("max_retries must be an integer from 0 to 20")
        object.__setattr__(self, "required_capabilities", _names(self.required_capabilities, "required_capabilities"))
        object.__setattr__(self, "required_permissions", _names(self.required_permissions, "required_permissions"))

    def to_dict(self) -> dict[str, object]:
        return {
            "idempotency_key": self.idempotency_key, "title": self.title,
            "payload": self.payload, "priority": self.priority, "owner_scope": self.owner_scope,
            "required_capabilities": list(self.required_capabilities),
            "required_permissions": list(self.required_permissions), "max_retries": self.max_retries,
        }

    @property
    def logical_sha256(self) -> str:
        return sha256_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MissionSpec":
        return cls(
            value.get("idempotency_key"), value.get("title"), value.get("payload"),
            value.get("priority", 50), value.get("owner_scope", "default"),
            value.get("required_capabilities", ()), value.get("required_permissions", ()),
            value.get("max_retries", 2),
        )


@dataclass(frozen=True, slots=True)
class AgentProfile:
    agent_id: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    ownership: tuple[str, ...]
    max_running: int = 1
    max_lease_seconds: int = 300
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _text(self.agent_id, "agent_id"))
        object.__setattr__(self, "capabilities", _names(self.capabilities, "capabilities"))
        object.__setattr__(self, "permissions", _names(self.permissions, "permissions"))
        object.__setattr__(self, "ownership", _names(self.ownership, "ownership"))
        if not self.ownership:
            raise ContractError("ownership must not be empty")
        if not isinstance(self.max_running, int) or isinstance(self.max_running, bool) or not 1 <= self.max_running <= 100:
            raise ContractError("max_running must be from 1 to 100")
        if not isinstance(self.max_lease_seconds, int) or isinstance(self.max_lease_seconds, bool) or not 1 <= self.max_lease_seconds <= 86_400:
            raise ContractError("max_lease_seconds must be from 1 to 86400")
        if not isinstance(self.active, bool):
            raise ContractError("active must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id, "capabilities": list(self.capabilities),
            "permissions": list(self.permissions), "ownership": list(self.ownership),
            "max_running": self.max_running, "max_lease_seconds": self.max_lease_seconds,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentProfile":
        return cls(
            value.get("agent_id"), value.get("capabilities", ()),
            value.get("permissions", ()), value.get("ownership", ()),
            value.get("max_running", 1), value.get("max_lease_seconds", 300), value.get("active", True),
        )


@dataclass(frozen=True, slots=True)
class CommitEvidence:
    sha: str
    repository: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "sha", _sha(self.sha, "commit.sha", (40, 64)))
        object.__setattr__(self, "repository", _text(self.repository, "commit.repository"))

    def to_dict(self) -> dict[str, str]: return {"sha": self.sha, "repository": self.repository}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommitEvidence": return cls(value.get("sha"), value.get("repository"))


@dataclass(frozen=True, slots=True)
class TestEvidence:
    name: str
    outcome: str
    command: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "test.name"))
        object.__setattr__(self, "command", _text(self.command, "test.command"))
        outcome = _text(self.outcome, "test.outcome").lower()
        if outcome not in {"passed", "failed", "skipped"}:
            raise ContractError("test.outcome must be passed, failed, or skipped")
        object.__setattr__(self, "outcome", outcome)

    def to_dict(self) -> dict[str, str]: return {"name": self.name, "outcome": self.outcome, "command": self.command}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TestEvidence": return cls(value.get("name"), value.get("outcome"), value.get("command"))


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    name: str
    sha256: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "artifact.name"))
        object.__setattr__(self, "sha256", _sha(self.sha256, "artifact.sha256", (64,)))
        path = _text(self.path, "artifact.path")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in path:
            raise ContractError("artifact.path must be a relative POSIX path")
        object.__setattr__(self, "path", path)

    def to_dict(self) -> dict[str, str]: return {"name": self.name, "sha256": self.sha256, "path": self.path}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactEvidence": return cls(value.get("name"), value.get("sha256"), value.get("path"))


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    summary: str
    commits: tuple[CommitEvidence, ...] = ()
    tests: tuple[TestEvidence, ...] = ()
    artifacts: tuple[ArtifactEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _text(self.summary, "evidence.summary"))
        for name in ("commits", "tests", "artifacts"):
            if len(getattr(self, name)) > MAX_ITEMS:
                raise ContractError(f"evidence.{name} exceeds {MAX_ITEMS} items")

    @property
    def sufficient(self) -> bool:
        tests_pass = bool(self.tests) and all(test.outcome == "passed" for test in self.tests)
        observable_output = bool(self.commits or self.artifacts)
        return tests_pass and observable_output

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary, "commits": [item.to_dict() for item in self.commits],
            "tests": [item.to_dict() for item in self.tests],
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    @property
    def sha256(self) -> str: return sha256_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompletionEvidence":
        def sequence(name: str) -> Sequence[Mapping[str, Any]]:
            raw = value.get(name, [])
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise ContractError(f"evidence.{name} must be an array")
            if any(not isinstance(item, Mapping) for item in raw):
                raise ContractError(f"evidence.{name} items must be objects")
            return tuple(raw)
        return cls(
            value.get("summary"), tuple(CommitEvidence.from_dict(item) for item in sequence("commits")),
            tuple(TestEvidence.from_dict(item) for item in sequence("tests")),
            tuple(ArtifactEvidence.from_dict(item) for item in sequence("artifacts")),
        )
