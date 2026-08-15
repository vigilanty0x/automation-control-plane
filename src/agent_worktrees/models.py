from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


class MissionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    REJECTED = "rejected"
    DONE = "done"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def normalize_owned_path(value: object) -> str:
    raw = _required_text(value, "owned path").replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"owned path must be repository-relative: {raw}")
    normalized = path.as_posix().rstrip("/")
    if normalized in {"", "."}:
        raise ValueError("owned path cannot be the repository root")
    if path.parts and path.parts[0] == ".git":
        raise ValueError("owned path cannot target .git")
    return normalized


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    items = tuple(_required_text(item, field_name) for item in value)
    if not items:
        raise ValueError(f"{field_name} must not be empty")
    return items


@dataclass(frozen=True)
class MissionRequest:
    task_id: str
    idempotency_key: str
    agent_id: str
    owner: str
    owned_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    base_ref: str = "main"
    max_attempts: int = 2
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("task_id", "idempotency_key", "agent_id", "owner", "base_ref"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        paths = tuple(dict.fromkeys(normalize_owned_path(path) for path in self.owned_paths))
        criteria = tuple(
            dict.fromkeys(
                _required_text(item, "acceptance criterion")
                for item in self.acceptance_criteria
            )
        )
        if not paths:
            raise ValueError("owned_paths must not be empty")
        if not criteria:
            raise ValueError("acceptance_criteria must not be empty")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        object.__setattr__(self, "owned_paths", paths)
        object.__setattr__(self, "acceptance_criteria", criteria)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MissionRequest:
        if not isinstance(data, Mapping):
            raise ValueError("request must be an object")
        return cls(
            task_id=data.get("task_id", ""),
            idempotency_key=data.get("idempotency_key", ""),
            agent_id=data.get("agent_id", ""),
            owner=data.get("owner", ""),
            owned_paths=tuple(data.get("owned_paths", ())),
            acceptance_criteria=tuple(data.get("acceptance_criteria", ())),
            base_ref=data.get("base_ref", "main"),
            max_attempts=data.get("max_attempts", 2),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "agent_id": self.agent_id,
            "owner": self.owner,
            "owned_paths": list(self.owned_paths),
            "acceptance_criteria": list(self.acceptance_criteria),
            "base_ref": self.base_ref,
            "max_attempts": self.max_attempts,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvidenceBundle:
    commit_sha: str
    tests: tuple[str, ...]
    artifacts: tuple[str, ...]
    criteria: Mapping[str, bool]
    produced_by: str
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "commit_sha", _required_text(self.commit_sha, "commit_sha"))
        if len(self.commit_sha) not in {40, 64} or re.fullmatch(r"[0-9a-fA-F]+", self.commit_sha) is None:
            raise ValueError("commit_sha must be a full 40- or 64-character hexadecimal object ID")
        object.__setattr__(self, "produced_by", _required_text(self.produced_by, "produced_by"))
        object.__setattr__(self, "tests", _string_tuple(self.tests, "tests"))
        object.__setattr__(self, "artifacts", _string_tuple(self.artifacts, "artifacts"))
        if not isinstance(self.criteria, Mapping) or not self.criteria:
            raise ValueError("criteria must be a non-empty object")
        normalized: dict[str, bool] = {}
        for key, value in self.criteria.items():
            name = _required_text(key, "criterion")
            if not isinstance(value, bool):
                raise ValueError("criterion results must be booleans")
            normalized[name] = value
        notes = tuple(_required_text(note, "note") for note in self.notes)
        object.__setattr__(self, "criteria", normalized)
        object.__setattr__(self, "notes", notes)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceBundle:
        if not isinstance(data, Mapping):
            raise ValueError("evidence must be an object")
        return cls(
            commit_sha=data.get("commit_sha", ""),
            tests=tuple(data.get("tests", ())),
            artifacts=tuple(data.get("artifacts", ())),
            criteria=data.get("criteria", {}),
            produced_by=data.get("produced_by", ""),
            notes=tuple(data.get("notes", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_sha": self.commit_sha,
            "tests": list(self.tests),
            "artifacts": list(self.artifacts),
            "criteria": dict(self.criteria),
            "produced_by": self.produced_by,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class MissionRecord:
    mission_id: str
    request: MissionRequest
    state: MissionState
    repo_root: str
    branch: str
    worktree_path: str
    attempt: int
    max_attempts: int
    last_error: str | None
    evidence: EvidenceBundle | None
    cleaned_at: str | None
    created_at: str
    updated_at: str

    @property
    def cleaned(self) -> bool:
        return self.cleaned_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "request": self.request.to_dict(),
            "state": self.state.value,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "evidence": None if self.evidence is None else self.evidence.to_dict(),
            "cleaned_at": self.cleaned_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
