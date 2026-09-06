"""Bounded schema-1.0 contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping


class ContractError(ValueError):
    pass


class MissionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    REJECTED = "rejected"
    DONE = "done"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


TERMINAL_STATES = {MissionState.FAILED, MissionState.REJECTED, MissionState.DONE}
TRANSITIONS = {
    MissionState.QUEUED: {MissionState.RUNNING, MissionState.REJECTED},
    MissionState.RUNNING: {MissionState.WAITING, MissionState.FAILED, MissionState.DONE},
    MissionState.WAITING: {MissionState.RUNNING, MissionState.FAILED, MissionState.REJECTED},
    MissionState.FAILED: set(), MissionState.REJECTED: set(), MissionState.DONE: set(),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def bounded_id(label: str, value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ContractError(f"{label} must contain 1 to 128 characters")
    return value


@dataclass(frozen=True, slots=True)
class BudgetVector:
    calls: int
    time_ms: int
    tokens: int

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0 or value > 10**15:
                raise ContractError(f"{name} must be a bounded non-negative integer")

    @classmethod
    def zero(cls) -> "BudgetVector":
        return cls(0, 0, 0)

    def add(self, other: "BudgetVector") -> "BudgetVector":
        return BudgetVector(self.calls + other.calls, self.time_ms + other.time_ms, self.tokens + other.tokens)

    def subtract(self, other: "BudgetVector") -> "BudgetVector":
        if not other.fits(self):
            raise ContractError("budget subtraction would become negative")
        return BudgetVector(self.calls - other.calls, self.time_ms - other.time_ms, self.tokens - other.tokens)

    def fits(self, limit: "BudgetVector") -> bool:
        return self.calls <= limit.calls and self.time_ms <= limit.time_ms and self.tokens <= limit.tokens

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BudgetVector":
        if set(value) != {"calls", "time_ms", "tokens"}:
            raise ContractError("budget vector fields do not match schema 1.0")
        return cls(value["calls"], value["time_ms"], value["tokens"])


@dataclass(frozen=True, slots=True)
class AgentProfile:
    agent_id: str
    owner: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    limit: BudgetVector
    max_retries: int

    def __post_init__(self) -> None:
        bounded_id("agent_id", self.agent_id); bounded_id("owner", self.owner)
        for label, values in (("capabilities", self.capabilities), ("permissions", self.permissions)):
            if len(values) > 64 or len(set(values)) != len(values) or any(not v or len(v) > 128 for v in values):
                raise ContractError(f"{label} must be a bounded unique list")
        if not 0 <= self.max_retries <= 100:
            raise ContractError("max_retries must be between 0 and 100")

    def to_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "owner": self.owner, "capabilities": list(self.capabilities),
                "permissions": list(self.permissions), "limit": self.limit.to_dict(), "max_retries": self.max_retries}


@dataclass(slots=True)
class Mission:
    mission_id: str
    agent_id: str
    required_capability: str
    required_permission: str
    limit: BudgetVector
    state: MissionState = MissionState.QUEUED
    retries: int = 0

    def __post_init__(self) -> None:
        bounded_id("mission_id", self.mission_id); bounded_id("agent_id", self.agent_id)
        bounded_id("required_capability", self.required_capability)
        bounded_id("required_permission", self.required_permission)
        if not isinstance(self.state, MissionState):
            self.state = MissionState(self.state)

    def transition(self, target: MissionState) -> None:
        if target not in TRANSITIONS[self.state]:
            raise ContractError(f"invalid mission transition: {self.state.value} -> {target.value}")
        self.state = target

    def to_dict(self) -> dict[str, Any]:
        return {"mission_id": self.mission_id, "agent_id": self.agent_id,
                "required_capability": self.required_capability, "required_permission": self.required_permission,
                "limit": self.limit.to_dict(), "state": self.state.value, "retries": self.retries}


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    mission_id: str
    agent_id: str
    amount: BudgetVector
    consumed: BudgetVector
    released: bool

    def to_dict(self) -> dict[str, Any]:
        return {"reservation_id": self.reservation_id, "mission_id": self.mission_id,
                "agent_id": self.agent_id, "amount": self.amount.to_dict(),
                "consumed": self.consumed.to_dict(), "released": self.released}


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: str
    decision: Decision
    action: str
    reason: str
    reservation: Reservation | None
    evidence_sha256: str

    @classmethod
    def create(cls, operation_id: str, decision: Decision, action: str, reason: str,
               reservation: Reservation | None = None) -> "OperationResult":
        bounded_id("operation_id", operation_id)
        identity = {"operation_id": operation_id, "decision": decision.value, "action": action,
                    "reason": reason, "reservation": reservation.to_dict() if reservation else None}
        return cls(operation_id, decision, action, reason[:512], reservation, sha256_json(identity))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "1.0", "operation_id": self.operation_id, "decision": self.decision.value,
                "action": self.action, "reason": self.reason,
                "reservation": self.reservation.to_dict() if self.reservation else None,
                "evidence_sha256": self.evidence_sha256}

