from __future__ import annotations

from model_router.models import EvidenceBundle, MissionState


class InvalidTransition(ValueError):
    pass


_ALLOWED: dict[MissionState, frozenset[MissionState]] = {
    MissionState.QUEUED: frozenset({MissionState.RUNNING, MissionState.REJECTED}),
    MissionState.RUNNING: frozenset(
        {MissionState.WAITING, MissionState.FAILED, MissionState.DONE}
    ),
    MissionState.WAITING: frozenset(
        {MissionState.RUNNING, MissionState.FAILED, MissionState.REJECTED}
    ),
    MissionState.FAILED: frozenset({MissionState.QUEUED, MissionState.REJECTED}),
    MissionState.REJECTED: frozenset(),
    MissionState.DONE: frozenset(),
}


def validate_transition(
    current: MissionState,
    target: MissionState,
    *,
    evidence: EvidenceBundle | None = None,
    attempt: int = 1,
    max_attempts: int = 1,
) -> None:
    if current is target:
        return
    if target not in _ALLOWED[current]:
        raise InvalidTransition(f"transition {current.value} -> {target.value} is not allowed")
    if current is MissionState.FAILED and target is MissionState.QUEUED:
        if attempt >= max_attempts:
            raise InvalidTransition("retry budget exhausted")
    if target is MissionState.DONE:
        _validate_evidence(evidence)


def _validate_evidence(evidence: EvidenceBundle | None) -> None:
    if evidence is None:
        raise InvalidTransition("done requires an evidence bundle")
    if len(evidence.commit_sha) < 7:
        raise InvalidTransition("evidence bundle requires a commit SHA")
    if not evidence.tests:
        raise InvalidTransition("evidence bundle requires at least one test result")
    if not evidence.artifacts:
        raise InvalidTransition("evidence bundle requires at least one artifact")
    if not evidence.produced_by.strip():
        raise InvalidTransition("evidence bundle requires a producer")
    if not evidence.criteria or not all(evidence.criteria.values()):
        raise InvalidTransition("all acceptance criteria must be proven")
