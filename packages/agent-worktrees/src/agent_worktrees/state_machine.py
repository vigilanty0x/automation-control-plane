from __future__ import annotations

from collections.abc import Iterable

from .models import EvidenceBundle, MissionState


class InvalidTransition(ValueError):
    """Raised when a mission state change violates the public contract."""


ALLOWED_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.QUEUED: frozenset({MissionState.RUNNING, MissionState.FAILED}),
    MissionState.RUNNING: frozenset(
        {MissionState.WAITING, MissionState.FAILED, MissionState.DONE}
    ),
    MissionState.WAITING: frozenset({MissionState.RUNNING, MissionState.FAILED}),
    MissionState.FAILED: frozenset({MissionState.QUEUED}),
    MissionState.REJECTED: frozenset(),
    MissionState.DONE: frozenset(),
}


def validate_transition(
    current: MissionState,
    target: MissionState,
    *,
    evidence: EvidenceBundle | None = None,
    declared_criteria: Iterable[str] = (),
    attempt: int = 1,
    max_attempts: int = 1,
) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot transition from {current.value} to {target.value}")
    if current == MissionState.FAILED and target == MissionState.QUEUED:
        if attempt >= max_attempts:
            raise InvalidTransition("retry budget exhausted")
    if target == MissionState.DONE:
        if evidence is None:
            raise InvalidTransition("done requires an evidence bundle")
        declared = set(declared_criteria)
        actual = set(evidence.criteria)
        if declared != actual or not all(evidence.criteria.values()):
            raise InvalidTransition("evidence must pass every declared acceptance criterion")
