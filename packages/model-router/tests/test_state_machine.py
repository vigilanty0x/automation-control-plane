from __future__ import annotations

import unittest

from model_router.models import EvidenceBundle, MissionState
from model_router.state_machine import InvalidTransition, validate_transition


def proof(**overrides: object) -> EvidenceBundle:
    values: dict[str, object] = {
        "commit_sha": "a" * 40,
        "tests": ("python -m unittest:pass",),
        "artifacts": ("dist/report.json",),
        "criteria": {"tests pass": True, "artifact present": True},
        "produced_by": "worker-1",
    }
    values.update(overrides)
    return EvidenceBundle(**values)


class StateMachineTests(unittest.TestCase):
    def test_happy_path_transitions(self) -> None:
        validate_transition(MissionState.QUEUED, MissionState.RUNNING)
        validate_transition(MissionState.RUNNING, MissionState.WAITING)
        validate_transition(MissionState.WAITING, MissionState.RUNNING)
        validate_transition(MissionState.RUNNING, MissionState.DONE, evidence=proof())

    def test_done_requires_machine_readable_evidence(self) -> None:
        with self.assertRaisesRegex(InvalidTransition, "evidence bundle"):
            validate_transition(MissionState.RUNNING, MissionState.DONE)

    def test_done_rejects_failed_acceptance_criterion(self) -> None:
        with self.assertRaisesRegex(InvalidTransition, "acceptance criteria"):
            validate_transition(
                MissionState.RUNNING,
                MissionState.DONE,
                evidence=proof(criteria={"tests pass": False}),
            )

    def test_done_rejects_missing_commit_tests_or_artifacts(self) -> None:
        for invalid in (
            proof(commit_sha=""),
            proof(tests=()),
            proof(artifacts=()),
        ):
            with self.assertRaises(InvalidTransition):
                validate_transition(MissionState.RUNNING, MissionState.DONE, evidence=invalid)

    def test_terminal_states_cannot_move(self) -> None:
        for state in (MissionState.DONE, MissionState.REJECTED):
            with self.assertRaises(InvalidTransition):
                validate_transition(state, MissionState.RUNNING)

    def test_invalid_shortcuts_are_blocked(self) -> None:
        with self.assertRaises(InvalidTransition):
            validate_transition(MissionState.QUEUED, MissionState.DONE, evidence=proof())
        with self.assertRaises(InvalidTransition):
            validate_transition(MissionState.WAITING, MissionState.DONE, evidence=proof())

    def test_failure_can_retry_only_inside_attempt_budget(self) -> None:
        validate_transition(
            MissionState.FAILED,
            MissionState.QUEUED,
            attempt=1,
            max_attempts=2,
        )
        with self.assertRaisesRegex(InvalidTransition, "retry budget"):
            validate_transition(
                MissionState.FAILED,
                MissionState.QUEUED,
                attempt=2,
                max_attempts=2,
            )

    def test_same_state_is_idempotent(self) -> None:
        validate_transition(MissionState.WAITING, MissionState.WAITING)


if __name__ == "__main__":
    unittest.main()
