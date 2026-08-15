from datetime import datetime, timezone
import unittest

from human_in_the_loop_queue import evaluate

GOOD = {"request_id": "req-1", "expires_at": "2099-12-31T00:00:00Z", "decision": "pending", "audit": [{"action": "created", "actor": "system", "at": "2026-08-14T00:00:00Z"}]}


def clock(value: str):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return lambda: parsed


class ContractTests(unittest.TestCase):
    def test_production_evaluation_uses_real_utc_clock(self):
        result = evaluate(GOOD)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["mode"], "production")
        self.assertFalse(result["authorization_evidence"])
        observed = datetime.fromisoformat(result["queue_record"]["as_of"])
        self.assertLess(abs((datetime.now(timezone.utc) - observed).total_seconds()), 5)

    def test_reviewer_poc_cannot_supply_its_own_past_clock(self):
        attack = {**GOOD, "expires_at": "2000-01-02T00:00:00Z", "as_of": "2000-01-01T00:00:00Z"}
        result = evaluate(attack)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["authorization_evidence"])

    def test_injected_past_clock_is_simulation_not_passed_evidence(self):
        result = evaluate(GOOD, clock=clock("2026-08-15T00:00:00Z"))
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["mode"], "simulation")
        self.assertEqual(result["queue_record"]["decision"], "pending")
        self.assertFalse(result["authorization_evidence"])

    def test_equal_clock_expires_request_in_simulation(self):
        record = {**GOOD, "expires_at": "2026-08-15T00:00:00Z", "decision": "expired"}
        result = evaluate(record, clock=clock("2026-08-15T00:00:00Z"))
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["queue_record"]["decision"], "expired")
        self.assertFalse(result["authorization_evidence"])
        self.assertEqual(evaluate({**record, "decision": "pending"}, clock=clock("2026-08-15T00:00:00Z"))["status"], "failed")

    def test_future_clock_requires_expired_decision(self):
        record = {**GOOD, "expires_at": "2026-08-15T00:00:00Z", "decision": "expired"}
        self.assertEqual(evaluate(record, clock=clock("2026-08-16T00:00:00Z"))["status"], "simulated")
        self.assertEqual(evaluate({**record, "decision": "pending"}, clock=clock("2026-08-16T00:00:00Z"))["status"], "failed")

    def test_naive_or_non_datetime_clock_fails(self):
        self.assertEqual(evaluate(GOOD, clock=lambda: datetime(2026, 8, 15))["status"], "failed")
        self.assertEqual(evaluate(GOOD, clock=lambda: "2026-08-15T00:00:00Z")["status"], "failed")

    def test_terminal_simulation_never_issues_authorization_evidence(self):
        record = {"request_id": "req-1", "expires_at": "2026-08-20T00:00:00Z", "decision": "approved", "audit": [{"action": "approved", "actor": "reviewer", "at": "2026-08-14T00:00:00Z"}]}
        result = evaluate(record, clock=clock("2026-08-15T00:00:00Z"))
        self.assertEqual(result["status"], "simulated")
        self.assertFalse(result["authorization_evidence"])

    def test_audited_pre_expiry_renewal_allows_simulated_decision(self):
        record = {"request_id": "req-1", "expires_at": "2026-08-10T00:00:00Z", "decision": "approved", "audit": [
            {"action": "renewed", "actor": "reviewer", "at": "2026-08-09T00:00:00Z", "new_expires_at": "2026-08-20T00:00:00Z"},
            {"action": "approved", "actor": "reviewer", "at": "2026-08-14T00:00:00Z"},
        ]}
        self.assertEqual(evaluate(record, clock=clock("2026-08-15T00:00:00Z"))["status"], "simulated")

    def test_late_renewal_and_future_audit_fail(self):
        late = {"request_id": "req-1", "expires_at": "2026-08-10T00:00:00Z", "decision": "pending", "audit": [{"action": "renewed", "actor": "reviewer", "at": "2026-08-11T00:00:00Z", "new_expires_at": "2026-08-20T00:00:00Z"}]}
        self.assertEqual(evaluate(late, clock=clock("2026-08-15T00:00:00Z"))["status"], "failed")
        future = {**GOOD, "audit": [{"action": "created", "actor": "system", "at": "2026-08-16T00:00:00Z"}]}
        self.assertEqual(evaluate(future, clock=clock("2026-08-15T00:00:00Z"))["status"], "failed")

    def test_missing_field_blocks_and_non_object_fails(self):
        record = dict(GOOD)
        record.pop("expires_at")
        self.assertEqual(evaluate(record)["status"], "blocked")
        self.assertEqual(evaluate(None)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
