"""Public API for Agent Inbox."""

from .contract import (
    AgentProfile, ArtifactEvidence, CommitEvidence, CompletionEvidence, ContractError,
    MissionSpec, MissionStatus, TestEvidence,
)
from .inbox import (
    AgentInbox, CapabilityMismatch, EvidenceRequired, IdempotencyConflict,
    LeaseConflict, MissionNotFound, NoMissionAvailable, StateConflict,
)

__all__ = [
    "AgentInbox", "AgentProfile", "ArtifactEvidence", "CapabilityMismatch",
    "CommitEvidence", "CompletionEvidence", "ContractError", "EvidenceRequired",
    "IdempotencyConflict", "LeaseConflict", "MissionNotFound", "MissionSpec",
    "MissionStatus", "NoMissionAvailable", "StateConflict", "TestEvidence",
]

__version__ = "0.1.0"

