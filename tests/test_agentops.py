from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from automation_control_plane.agentops import (
    evaluate_routing,
    inventory,
    plan_context,
    project_inbox,
    record_session,
    simulate_circuit,
    simulate_quota,
    verify_session,
)
from automation_control_plane.agentops.cli import main


class InventoryTests(unittest.TestCase):
    def test_inventory_is_exact_and_prepared_not_released(self) -> None:
        result = inventory()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["details"]["source_count"], 13)
        self.assertEqual(result["details"]["selected_base"], "automation-control-plane")
        self.assertEqual(result["details"]["gates"]["release"], "blocked")
        self.assertEqual(result["details"]["gates"]["source_archive"], "blocked")

    def test_inventory_evidence_is_deterministic(self) -> None:
        self.assertEqual(inventory()["evidence_sha256"], inventory()["evidence_sha256"])


class RoutingTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "agents": [
                {
                    "id": "planner",
                    "healthy": True,
                    "owner": "ops",
                    "capabilities": ["plan"],
                },
                {
                    "id": "worker",
                    "healthy": True,
                    "owner": "runtime",
                    "capabilities": ["execute", "report"],
                },
            ],
            "routes": [
                {
                    "source": "planner",
                    "target": "worker",
                    "capability": "execute",
                    "owner": "runtime",
                }
            ],
            "required_capabilities": ["execute"],
        }

    def test_valid_route_passes(self) -> None:
        result = evaluate_routing(self.payload())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["details"]["active_route_count"], 1)

    def test_unhealthy_agent_fails_without_becoming_blocked(self) -> None:
        payload = self.payload()
        payload["agents"][1]["healthy"] = False
        result = evaluate_routing(payload)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["unhealthy_agents"], ["worker"])

    def test_route_owner_mismatch_is_blocked(self) -> None:
        payload = self.payload()
        payload["routes"][0]["owner"] = "ops"
        result = evaluate_routing(payload)
        self.assertEqual(result["status"], "blocked")

    def test_duplicate_route_is_blocked(self) -> None:
        payload = self.payload()
        payload["routes"].append(dict(payload["routes"][0]))
        self.assertEqual(evaluate_routing(payload)["status"], "blocked")

    def test_missing_required_capability_fails(self) -> None:
        payload = self.payload()
        payload["required_capabilities"] = ["report"]
        result = evaluate_routing(payload)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["missing_required_capabilities"], ["report"])


class ContextTests(unittest.TestCase):
    def test_required_then_priority_planning(self) -> None:
        result = plan_context(
            {
                "window_tokens": 100,
                "reserve_output_tokens": 20,
                "sections": [
                    {"id": "system", "tokens": 30, "required": True, "priority": 0},
                    {"id": "large", "tokens": 60, "required": False, "priority": 10},
                    {"id": "small", "tokens": 20, "required": False, "priority": 10},
                    {"id": "low", "tokens": 25, "required": False, "priority": 1},
                ],
            }
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["details"]["included"], ["system", "small", "low"])
        self.assertEqual(result["details"]["excluded"], ["large"])

    def test_required_overflow_fails(self) -> None:
        result = plan_context(
            {
                "window_tokens": 20,
                "reserve_output_tokens": 10,
                "sections": [
                    {"id": "required", "tokens": 11, "required": True, "priority": 0}
                ],
            }
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["deficit_tokens"], 1)

    def test_boolean_is_not_an_integer(self) -> None:
        result = plan_context(
            {
                "window_tokens": True,
                "reserve_output_tokens": 0,
                "sections": [],
            }
        )
        self.assertEqual(result["status"], "blocked")


class QuotaTests(unittest.TestCase):
    def test_required_and_optional_admission(self) -> None:
        result = simulate_quota(
            {
                "budgets": {"tokens": 100, "time_ms": 100, "micro_cost": 100},
                "tasks": [
                    {
                        "id": "required",
                        "priority": 0,
                        "required": True,
                        "tokens": 50,
                        "time_ms": 10,
                        "micro_cost": 20,
                    },
                    {
                        "id": "high",
                        "priority": 10,
                        "required": False,
                        "tokens": 40,
                        "time_ms": 20,
                        "micro_cost": 30,
                    },
                    {
                        "id": "low",
                        "priority": 1,
                        "required": False,
                        "tokens": 20,
                        "time_ms": 20,
                        "micro_cost": 10,
                    },
                ],
            }
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["details"]["admitted"], ["required", "high"])
        self.assertEqual(result["details"]["rejected"], ["low"])

    def test_required_deficit_fails_closed(self) -> None:
        result = simulate_quota(
            {
                "budgets": {"tokens": 1, "time_ms": 1, "micro_cost": 1},
                "tasks": [
                    {
                        "id": "required",
                        "priority": 0,
                        "required": True,
                        "tokens": 2,
                        "time_ms": 1,
                        "micro_cost": 1,
                    }
                ],
            }
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["deficits"], {"tokens": 1})

    def test_duplicate_task_is_blocked(self) -> None:
        task = {
            "id": "same",
            "priority": 0,
            "required": False,
            "tokens": 0,
            "time_ms": 0,
            "micro_cost": 0,
        }
        result = simulate_quota(
            {
                "budgets": {"tokens": 1, "time_ms": 1, "micro_cost": 1},
                "tasks": [task, dict(task)],
            }
        )
        self.assertEqual(result["status"], "blocked")


class SessionTests(unittest.TestCase):
    def session(self) -> dict:
        return {
            "session_id": "session-a",
            "events": [
                {
                    "event_id": "event-a",
                    "actor": "planner",
                    "type": "planned",
                    "at": "2026-08-18T01:00:00Z",
                    "data": {"task": "bounded"},
                },
                {
                    "event_id": "event-b",
                    "actor": "worker",
                    "type": "completed",
                    "at": "2026-08-18T01:00:01Z",
                    "data": {"tests": 3},
                },
            ],
        }

    def test_record_and_authenticity_bound_verify(self) -> None:
        recorded = record_session(self.session())
        self.assertEqual(recorded["status"], "passed")
        details = recorded["details"]
        verified = verify_session(
            {
                "session_id": "session-a",
                "chain": details["chain"],
                "expected_initial_sha256": details["initial_previous_sha256"],
                "expected_head_sha256": details["head_sha256"],
            }
        )
        self.assertEqual(verified["status"], "passed")
        self.assertEqual(verified["details"]["authenticity_status"], "verified")

    def test_tampering_is_failed_not_passed(self) -> None:
        recorded = record_session(self.session())
        chain = json.loads(json.dumps(recorded["details"]["chain"]))
        chain[0]["event"]["data"]["task"] = "changed"
        result = verify_session({"session_id": "session-a", "chain": chain})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["mismatch"]["reason"], "event_hash_mismatch")

    def test_sensitive_key_is_blocked(self) -> None:
        payload = self.session()
        payload["events"][0]["data"] = {"api_key": "synthetic"}
        self.assertEqual(record_session(payload)["status"], "blocked")

    def test_non_increasing_timestamp_is_blocked(self) -> None:
        payload = self.session()
        payload["events"][1]["at"] = payload["events"][0]["at"]
        self.assertEqual(record_session(payload)["status"], "blocked")

    def test_nested_access_token_key_is_blocked(self) -> None:
        payload = self.session()
        payload["events"][0]["data"] = {"auth": {"access_token": "synthetic"}}
        self.assertEqual(record_session(payload)["status"], "blocked")

    def test_recomputed_chain_with_reversed_time_fails_semantics(self) -> None:
        first = self.session()["events"][0]
        second = self.session()["events"][1]
        second = {**second, "at": "2026-08-18T00:59:59Z"}
        import automation_control_plane.agentops.sessions as sessions
        previous = "0" * 64
        chain = []
        for index, event in enumerate([first, second]):
            body = {"session_id": "session-a", "index": index, "previous_sha256": previous, "event": event}
            event_sha = sessions.json_sha256(body)
            chain.append({**body, "event_sha256": event_sha})
            previous = event_sha
        result = verify_session({"session_id": "session-a", "chain": chain})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["mismatch"]["reason"], "timestamp_order_mismatch")


class CircuitTests(unittest.TestCase):
    def test_complete_closed_open_half_open_closed_trace(self) -> None:
        result = simulate_circuit(
            {
                "policy": {
                    "failure_threshold": 2,
                    "success_threshold": 2,
                    "max_events": 10,
                },
                "initial_state": "closed",
                "events": [
                    {"id": "f1", "outcome": "failure"},
                    {"id": "f2", "outcome": "failure"},
                    {"id": "cool", "outcome": "cooldown_elapsed"},
                    {"id": "s1", "outcome": "success"},
                    {"id": "s2", "outcome": "success"},
                ],
            }
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["details"]["final_state"], "closed")

    def test_open_rejects_outcome_before_cooldown(self) -> None:
        result = simulate_circuit(
            {
                "policy": {
                    "failure_threshold": 1,
                    "success_threshold": 1,
                    "max_events": 2,
                },
                "initial_state": "open",
                "events": [{"id": "bad", "outcome": "success"}],
            }
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["details"]["invalid_transition"]["reason"],
            "open_state_requires_cooldown_elapsed",
        )


class InboxTests(unittest.TestCase):
    def test_projection_orders_overdue_then_failure_then_approval(self) -> None:
        result = project_inbox(
            {
                "now_epoch_ms": 100,
                "jobs": [
                    {
                        "job_id": "approval",
                        "workflow_id": "wf",
                        "state": "waiting_approval",
                        "priority": 5,
                        "deadline_epoch_ms": 200,
                        "approval_required": True,
                        "last_error": None,
                    },
                    {
                        "job_id": "failed",
                        "workflow_id": "wf",
                        "state": "failed",
                        "priority": 1,
                        "deadline_epoch_ms": None,
                        "approval_required": False,
                        "last_error": "synthetic failure",
                    },
                    {
                        "job_id": "late",
                        "workflow_id": "wf",
                        "state": "running",
                        "priority": 0,
                        "deadline_epoch_ms": 99,
                        "approval_required": False,
                        "last_error": None,
                    },
                    {
                        "job_id": "done",
                        "workflow_id": "wf",
                        "state": "succeeded",
                        "priority": 100,
                        "deadline_epoch_ms": 1,
                        "approval_required": False,
                        "last_error": None,
                    },
                ],
            }
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            [item["job_id"] for item in result["details"]["items"]],
            ["late", "failed", "approval"],
        )
        self.assertFalse(result["details"]["mutation_performed"])

    def test_invalid_state_is_blocked(self) -> None:
        result = project_inbox(
            {
                "now_epoch_ms": 0,
                "jobs": [
                    {
                        "job_id": "job",
                        "workflow_id": "wf",
                        "state": "invented",
                        "priority": 0,
                        "deadline_epoch_ms": None,
                        "approval_required": False,
                        "last_error": None,
                    }
                ],
            }
        )
        self.assertEqual(result["status"], "blocked")


class CliTests(unittest.TestCase):
    def test_inventory_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["inventory"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["kind"], "inventory")

    def test_file_command_and_failure_exit_code(self) -> None:
        payload = {
            "window_tokens": 10,
            "reserve_output_tokens": 9,
            "sections": [
                {"id": "required", "tokens": 2, "required": True, "priority": 0}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["context", "--input", str(path)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    def test_duplicate_json_member_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"window_tokens":10,"window_tokens":11}', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["context", "--input", str(path)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
