from __future__ import annotations

import unittest

from agent_worktrees.models import EvidenceBundle, MissionState
from agent_worktrees.state_machine import InvalidTransition, validate_transition


def proof(**overrides: object) -> EvidenceBundle:
    values: dict[str, object] = {
        "commit_sha": "c" * 40,
        "tests": ("unit:pass",),
        "artifacts": ("artifacts/report.json",),
        "criteria": {"tests pass": True},
        "produced_by": "agent-a",
    }
    values.update(overrides)
    return EvidenceBundle(**values)


class StateMachineTests(unittest.TestCase):
    def test_happy_path_and_wait_resume(self) -> None:
        validate_transition(MissionState.QUEUED, MissionState.RUNNING)
        validate_transition(MissionState.RUNNING, MissionState.WAITING)
        validate_transition(MissionState.WAITING, MissionState.RUNNING)
        validate_transition(
            MissionState.RUNNING,
            MissionState.DONE,
            evidence=proof(),
            declared_criteria=("tests pass",),
        )

    def test_done_requires_complete_matching_evidence(self) -> None:
        with self.assertRaisesRegex(InvalidTransition, "evidence"):
            validate_transition(MissionState.RUNNING, MissionState.DONE)
        with self.assertRaisesRegex(InvalidTransition, "criterion"):
            validate_transition(
                MissionState.RUNNING,
                MissionState.DONE,
                evidence=proof(criteria={"different": True}),
                declared_criteria=("tests pass",),
            )

    def test_retry_is_bounded(self) -> None:
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

    def test_terminal_states_cannot_move(self) -> None:
        for state in (MissionState.DONE, MissionState.REJECTED):
            with self.subTest(state=state), self.assertRaises(InvalidTransition):
                validate_transition(state, MissionState.RUNNING)

    def test_invalid_shortcuts_are_rejected(self) -> None:
        with self.assertRaises(InvalidTransition):
            validate_transition(MissionState.QUEUED, MissionState.DONE, evidence=proof())
        with self.assertRaises(InvalidTransition):
            validate_transition(MissionState.WAITING, MissionState.DONE, evidence=proof())


if __name__ == "__main__":
    unittest.main()
