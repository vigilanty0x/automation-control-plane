from __future__ import annotations

import unittest

from automation_control_plane.agentops.adapters import rehearse_adapter


SHAS = {
    "agentmesh": "320f5116f6582519d1609ce87287fd9ff7267eb3",
    "context-window-budgeter": "35bb3e05d05ad870715b740143c429f08eda25e7",
    "agent-quota-simulator": "e99000cecf12432365e8ccfc8fa6e4b1d18ad15f",
    "agent-session-recorder": "2363c4efe0c61158c523a6dfc3d29cb3d7af1c54",
    "circuit-breaker-lab": "2924dfb6eed8a208788491fa1d50fa6bd99e4359",
}


class CandidateAdapterTests(unittest.TestCase):
    def assert_rehearsal_only(self, result: dict) -> None:
        self.assertEqual(result["status"], "passed")
        details = result["details"]
        self.assertTrue(details["rehearsal_only"])
        self.assertFalse(details["alias_activated"])
        self.assertFalse(details["migration_performed"])
        self.assertFalse(details["consumer_mutation_performed"])
        self.assertFalse(details["source_retirement_authorized"])

    def test_agentmesh_requires_explicit_identity_mapping_and_preserves_counts(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agentmesh",
                "source_sha": SHAS["agentmesh"],
                "source_payload": {"agent_count": 2, "healthy_agents": 2, "route_count": 1},
                "adapter_input": {
                    "agents": [
                        {"id": "a", "healthy": True, "owner": "team-a", "capabilities": ["chat"]},
                        {"id": "b", "healthy": True, "owner": "team-b", "capabilities": ["code"]},
                    ],
                    "routes": [
                        {"source": "a", "target": "b", "capability": "code", "owner": "team-b"}
                    ],
                    "required_capabilities": ["code"],
                },
            }
        )
        self.assert_rehearsal_only(result)
        self.assertTrue(result["details"]["count_match"])

    def test_agentmesh_blocks_missing_explicit_mapping(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agentmesh",
                "source_sha": SHAS["agentmesh"],
                "source_payload": {"agent_count": 1, "healthy_agents": 1, "route_count": 1},
            }
        )
        self.assertEqual(result["status"], "blocked")

    def test_context_adapter_preserves_source_name_tie_break_even_when_target_size_would_reorder(self) -> None:
        # Source order for equal priority is name: a then b. Native target order
        # would prefer b first because it is smaller. The adapter assigns unique
        # internal priorities so the source selection remains exact.
        result = rehearse_adapter(
            {
                "source_repository": "context-window-budgeter",
                "source_sha": SHAS["context-window-budgeter"],
                "source_payload": {
                    "window_tokens": 8,
                    "output_reserve": 2,
                    "sections": [
                        {"name": "a", "tokens": 4, "required": False, "priority": 10},
                        {"name": "b", "tokens": 2, "required": False, "priority": 10},
                        {"name": "system", "tokens": 2, "required": True},
                    ],
                },
            }
        )
        self.assert_rehearsal_only(result)
        self.assertEqual(result["details"]["source_plan"]["selected"], ["system", "a"])
        self.assertEqual(result["details"]["source_plan"]["dropped"], ["b"])
        self.assertTrue(result["details"]["selection_match"])

    def test_quota_adapter_exactly_converts_seconds_to_milliseconds(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agent-quota-simulator",
                "source_sha": SHAS["agent-quota-simulator"],
                "source_payload": {
                    "budget": {"tokens": 10, "seconds": 5, "cost_micros": 20},
                    "tasks": [
                        {"id": "a", "tokens": 4, "seconds": 3, "cost_micros": 10, "priority": 10},
                        {"id": "b", "tokens": 7, "seconds": 2, "cost_micros": 5, "priority": 5},
                    ],
                },
            }
        )
        self.assert_rehearsal_only(result)
        self.assertEqual(result["details"]["source_admitted"], ["a"])
        self.assertEqual(result["details"]["source_rejected"], ["b"])
        self.assertTrue(result["details"]["selection_match"])
        self.assertTrue(result["details"]["remaining_match"])
        self.assertEqual(result["details"]["target_payload"]["budgets"]["time_ms"], 5000)
        self.assertFalse(result["details"]["target_payload"]["tasks"][0]["required"])

    def test_quota_adapter_blocks_unrepresentable_exact_unit_conversion(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agent-quota-simulator",
                "source_sha": SHAS["agent-quota-simulator"],
                "source_payload": {
                    "budget": {"tokens": 1, "seconds": 1_000_000_001, "cost_micros": 1},
                    "tasks": [],
                },
            }
        )
        self.assertEqual(result["status"], "blocked")

    def test_session_adapter_adds_explicit_identity_and_time_without_transferring_authenticity(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agent-session-recorder",
                "source_sha": SHAS["agent-session-recorder"],
                "source_payload": {
                    "events": [
                        {"sequence": 1, "kind": "input", "content": {"text": "hello"}},
                        {"sequence": 2, "kind": "output", "content": {"text": "world"}},
                    ]
                },
                "adapter_input": {
                    "session_id": "legacy-session",
                    "actor": "legacy-agent",
                    "timestamps": ["2026-08-18T12:00:00Z", "2026-08-18T12:00:01Z"],
                },
            }
        )
        self.assert_rehearsal_only(result)
        self.assertEqual(result["details"]["source_event_count"], 2)
        self.assertEqual(result["details"]["target_event_count"], 2)
        self.assertFalse(result["details"]["authenticity_transferred"])
        self.assertFalse(result["details"]["source_head_reused"])

    def test_session_adapter_fails_closed_on_sensitive_source_content(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "agent-session-recorder",
                "source_sha": SHAS["agent-session-recorder"],
                "source_payload": {"events": [{"sequence": 1, "kind": "input", "content": {"token": "synthetic"}}]},
                "adapter_input": {
                    "session_id": "legacy-session",
                    "actor": "legacy-agent",
                    "timestamps": ["2026-08-18T12:00:00Z"],
                },
            }
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["target_result"]["status"], "blocked")

    def test_circuit_adapter_emits_cooldown_only_from_elapsed_time_evidence(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "circuit-breaker-lab",
                "source_sha": SHAS["circuit-breaker-lab"],
                "source_payload": {
                    "threshold": 2,
                    "cooldown_ms": 1000,
                    "events": [
                        {"at_ms": 0, "success": False},
                        {"at_ms": 10, "success": False},
                        {"at_ms": 500, "success": True},
                        {"at_ms": 1010, "success": True},
                    ],
                },
            }
        )
        self.assert_rehearsal_only(result)
        self.assertEqual(result["details"]["suppressed_open_state_attempts"], 1)
        outcomes = [event["outcome"] for event in result["details"]["target_payload"]["events"]]
        self.assertEqual(outcomes, ["failure", "failure", "cooldown_elapsed", "success"])
        self.assertTrue(result["details"]["final_state_match"])
        self.assertEqual(result["details"]["source_final_state"], "closed")

    def test_wrong_source_sha_blocks_every_adapter(self) -> None:
        result = rehearse_adapter(
            {
                "source_repository": "context-window-budgeter",
                "source_sha": "0" * 40,
                "source_payload": {"window_tokens": 10, "output_reserve": 1, "sections": []},
            }
        )
        self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
