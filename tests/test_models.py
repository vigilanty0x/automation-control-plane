from __future__ import annotations

import unittest

from agent_worktrees.models import EvidenceBundle, MissionRequest, MissionState


class MissionRequestTests(unittest.TestCase):
    def test_normalizes_owned_paths(self) -> None:
        request = MissionRequest.from_dict(
            {
                "task_id": "task-1",
                "idempotency_key": "idem-1",
                "agent_id": "agent-a",
                "owner": "team-a",
                "owned_paths": ["src/api/", "tests/test_api.py"],
                "acceptance_criteria": ["tests pass"],
            }
        )
        self.assertEqual(request.owned_paths, ("src/api", "tests/test_api.py"))

    def test_rejects_absolute_parent_and_git_paths(self) -> None:
        for path in ("/etc/passwd", "../secret", "src/../../secret", ".git/config"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                MissionRequest.from_dict(
                    {
                        "task_id": "task-1",
                        "idempotency_key": "idem-1",
                        "agent_id": "agent-a",
                        "owner": "team-a",
                        "owned_paths": [path],
                        "acceptance_criteria": ["tests pass"],
                    }
                )

    def test_requires_owner_scope_and_criteria(self) -> None:
        base = {
            "task_id": "task-1",
            "idempotency_key": "idem-1",
            "agent_id": "agent-a",
            "owner": "team-a",
            "owned_paths": ["src"],
            "acceptance_criteria": ["tests pass"],
        }
        for field in ("owner", "owned_paths", "acceptance_criteria"):
            invalid = {**base, field: [] if field != "owner" else ""}
            with self.subTest(field=field), self.assertRaises(ValueError):
                MissionRequest.from_dict(invalid)

    def test_serialization_round_trip_is_stable(self) -> None:
        request = MissionRequest.from_dict(
            {
                "task_id": "task-1",
                "idempotency_key": "idem-1",
                "agent_id": "Agent A",
                "owner": "team-a",
                "base_ref": "main",
                "owned_paths": ["src"],
                "acceptance_criteria": ["tests pass"],
                "max_attempts": 3,
                "metadata": {"priority": "high"},
            }
        )
        self.assertEqual(MissionRequest.from_dict(request.to_dict()), request)


class EvidenceBundleTests(unittest.TestCase):
    def test_requires_machine_readable_proof(self) -> None:
        valid = {
            "commit_sha": "a" * 40,
            "tests": ["unit:pass"],
            "artifacts": ["artifacts/report.json"],
            "criteria": {"tests pass": True},
            "produced_by": "agent-a",
        }
        for field in ("commit_sha", "tests", "artifacts", "criteria", "produced_by"):
            invalid = {**valid, field: "" if field in {"commit_sha", "produced_by"} else []}
            with self.subTest(field=field), self.assertRaises(ValueError):
                EvidenceBundle.from_dict(invalid)

        with self.assertRaisesRegex(ValueError, "object ID"):
            EvidenceBundle.from_dict({**valid, "commit_sha": "not-a-sha"})

    def test_round_trip(self) -> None:
        evidence = EvidenceBundle.from_dict(
            {
                "commit_sha": "b" * 40,
                "tests": ["unit:pass"],
                "artifacts": ["artifacts/report.json"],
                "criteria": {"tests pass": True},
                "produced_by": "agent-a",
                "notes": ["synthetic fixture"],
            }
        )
        self.assertEqual(EvidenceBundle.from_dict(evidence.to_dict()), evidence)


class StateTests(unittest.TestCase):
    def test_public_states_are_complete(self) -> None:
        self.assertEqual(
            {state.value for state in MissionState},
            {"queued", "running", "waiting", "failed", "rejected", "done"},
        )


if __name__ == "__main__":
    unittest.main()
