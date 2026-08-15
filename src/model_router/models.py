from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MissionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    REJECTED = "rejected"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class AgentProfile:
    agent_id: str
    owner: str
    capabilities: frozenset[str]
    permissions: frozenset[str]
    cost_per_1k_tokens_usd: float
    p95_latency_ms: int
    context_window_tokens: int
    historical_success_rate: float
    active: bool = True
    current_load: int = 0
    max_concurrency: int = 1
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be blank")
        if not self.owner.strip():
            raise ValueError("owner must not be blank")
        if self.cost_per_1k_tokens_usd < 0:
            raise ValueError("cost_per_1k_tokens_usd must be non-negative")
        if self.p95_latency_ms <= 0:
            raise ValueError("p95_latency_ms must be positive")
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if not 0 <= self.historical_success_rate <= 1:
            raise ValueError("historical_success_rate must be between 0 and 1")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if self.current_load < 0:
            raise ValueError("current_load must be non-negative")

    def estimated_cost(self, context_tokens: int) -> float:
        return round((context_tokens / 1_000) * self.cost_per_1k_tokens_usd, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "owner": self.owner,
            "capabilities": sorted(self.capabilities),
            "permissions": sorted(self.permissions),
            "cost_per_1k_tokens_usd": self.cost_per_1k_tokens_usd,
            "p95_latency_ms": self.p95_latency_ms,
            "context_window_tokens": self.context_window_tokens,
            "historical_success_rate": self.historical_success_rate,
            "active": self.active,
            "current_load": self.current_load,
            "max_concurrency": self.max_concurrency,
            "metadata": dict(sorted(self.metadata.items())),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentProfile:
        return cls(
            agent_id=str(payload["agent_id"]),
            owner=str(payload["owner"]),
            capabilities=frozenset(str(item) for item in payload["capabilities"]),
            permissions=frozenset(str(item) for item in payload["permissions"]),
            cost_per_1k_tokens_usd=float(payload["cost_per_1k_tokens_usd"]),
            p95_latency_ms=int(payload["p95_latency_ms"]),
            context_window_tokens=int(payload["context_window_tokens"]),
            historical_success_rate=float(payload["historical_success_rate"]),
            active=bool(payload.get("active", True)),
            current_load=int(payload.get("current_load", 0)),
            max_concurrency=int(payload.get("max_concurrency", 1)),
            metadata={str(k): str(v) for k, v in payload.get("metadata", {}).items()},
        )


@dataclass(frozen=True, slots=True)
class TaskRequest:
    task_id: str
    idempotency_key: str
    title: str
    required_capabilities: frozenset[str]
    required_permissions: frozenset[str]
    budget_usd: float
    max_latency_ms: int
    context_tokens: int
    scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    risk: RiskLevel = RiskLevel.LOW
    max_attempts: int = 2
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if not self.required_capabilities:
            raise ValueError("at least one required capability is required")
        if self.budget_usd <= 0:
            raise ValueError("budget_usd must be positive")
        if self.max_latency_ms <= 0:
            raise ValueError("max_latency_ms must be positive")
        if self.context_tokens <= 0:
            raise ValueError("context_tokens must be positive")
        if not self.scope or any(not item.strip() for item in self.scope):
            raise ValueError("scope must contain explicit non-empty paths")
        if not self.acceptance_criteria or any(
            not item.strip() for item in self.acceptance_criteria
        ):
            raise ValueError("acceptance_criteria must be measurable and non-empty")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "idempotency_key": self.idempotency_key,
            "title": self.title,
            "required_capabilities": sorted(self.required_capabilities),
            "required_permissions": sorted(self.required_permissions),
            "budget_usd": self.budget_usd,
            "max_latency_ms": self.max_latency_ms,
            "context_tokens": self.context_tokens,
            "scope": list(self.scope),
            "acceptance_criteria": list(self.acceptance_criteria),
            "risk": self.risk.value,
            "max_attempts": self.max_attempts,
            "metadata": dict(sorted(self.metadata.items())),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskRequest:
        return cls(
            task_id=str(payload["task_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            title=str(payload["title"]),
            required_capabilities=frozenset(
                str(item) for item in payload["required_capabilities"]
            ),
            required_permissions=frozenset(
                str(item) for item in payload.get("required_permissions", [])
            ),
            budget_usd=float(payload["budget_usd"]),
            max_latency_ms=int(payload["max_latency_ms"]),
            context_tokens=int(payload["context_tokens"]),
            scope=tuple(str(item) for item in payload["scope"]),
            acceptance_criteria=tuple(str(item) for item in payload["acceptance_criteria"]),
            risk=RiskLevel(str(payload.get("risk", "low"))),
            max_attempts=int(payload.get("max_attempts", 2)),
            metadata={str(k): str(v) for k, v in payload.get("metadata", {}).items()},
        )


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    commit_sha: str
    tests: tuple[str, ...]
    artifacts: tuple[str, ...]
    criteria: dict[str, bool]
    produced_by: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_sha": self.commit_sha,
            "tests": list(self.tests),
            "artifacts": list(self.artifacts),
            "criteria": dict(sorted(self.criteria.items())),
            "produced_by": self.produced_by,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceBundle:
        return cls(
            commit_sha=str(payload.get("commit_sha", "")),
            tests=tuple(str(item) for item in payload.get("tests", [])),
            artifacts=tuple(str(item) for item in payload.get("artifacts", [])),
            criteria={str(k): bool(v) for k, v in payload.get("criteria", {}).items()},
            produced_by=str(payload.get("produced_by", "")),
            notes=tuple(str(item) for item in payload.get("notes", [])),
        )


@dataclass(frozen=True, slots=True)
class RouteDecision:
    task_id: str
    selected_agent_id: str | None
    selected_owner: str | None
    score: float
    estimated_cost_usd: float | None
    candidate_scores: dict[str, float]
    rejections: dict[str, list[str]]
    explanations: tuple[str, ...]
    disagreement: bool = False
    human_approval_required: bool = False
    escalation_reason: str | None = None

    @property
    def rejected(self) -> bool:
        return self.selected_agent_id is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "selected_agent_id": self.selected_agent_id,
            "selected_owner": self.selected_owner,
            "score": self.score,
            "estimated_cost_usd": self.estimated_cost_usd,
            "candidate_scores": dict(sorted(self.candidate_scores.items())),
            "rejections": {key: list(value) for key, value in sorted(self.rejections.items())},
            "explanations": list(self.explanations),
            "disagreement": self.disagreement,
            "human_approval_required": self.human_approval_required,
            "escalation_reason": self.escalation_reason,
            "rejected": self.rejected,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouteDecision:
        return cls(
            task_id=str(payload["task_id"]),
            selected_agent_id=(
                str(payload["selected_agent_id"])
                if payload.get("selected_agent_id") is not None
                else None
            ),
            selected_owner=(
                str(payload["selected_owner"])
                if payload.get("selected_owner") is not None
                else None
            ),
            score=float(payload["score"]),
            estimated_cost_usd=(
                float(payload["estimated_cost_usd"])
                if payload.get("estimated_cost_usd") is not None
                else None
            ),
            candidate_scores={
                str(k): float(v) for k, v in payload.get("candidate_scores", {}).items()
            },
            rejections={
                str(k): [str(item) for item in v]
                for k, v in payload.get("rejections", {}).items()
            },
            explanations=tuple(str(item) for item in payload.get("explanations", [])),
            disagreement=bool(payload.get("disagreement", False)),
            human_approval_required=bool(payload.get("human_approval_required", False)),
            escalation_reason=(
                str(payload["escalation_reason"])
                if payload.get("escalation_reason") is not None
                else None
            ),
        )
