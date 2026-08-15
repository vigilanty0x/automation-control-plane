import json
import math
import subprocess
import sys
import unittest

from automation_control_plane.core import run, transition


class ControlPlaneTests(unittest.TestCase):
    def job(self, state="pending", version=1):
        return {"id": "job-1", "version": version, "action": "publish-report", "state": state, "spent": 0, "budget": 10}

    def current(self, job):
        return {"id": job["id"], "version": job["version"], "state": job["state"]}

    def approval(self, job):
        return {"job_id": job["id"], "version": job["version"], "action": job["action"], "approved_by": "owner"}

    def test_approval_is_bound_to_job_version_and_action(self):
        job = self.job()
        result = transition(job, "approved", principal="owner", capabilities=["approve"], current_state=self.current(job), approval=self.approval(job))
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["version"], 2)
        bad = self.approval(job)
        bad["version"] = 2
        self.assertEqual(transition(job, "approved", principal="owner", capabilities=["approve"], current_state=self.current(job), approval=bad)["reason"], "approval_mismatch")

    def test_trusted_current_state_must_match(self):
        job = self.job("approved")
        current = self.current(job)
        current["version"] = 2
        self.assertEqual(transition(job, "running", principal="runner", capabilities=["transition"], current_state=current)["reason"], "stale_state")
        current["version"] = -1
        with self.assertRaises(ValueError):
            transition(job, "running", principal="runner", capabilities=["transition"], current_state=current)

    def test_budget_types_and_bounds_fail_closed(self):
        for value in (True, -1, math.inf):
            job = self.job("approved")
            job["budget"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                transition(job, "running", principal="runner", capabilities=["transition"], current_state=self.current(job))

    def test_budget_exhaustion_blocks_run(self):
        job = self.job("approved")
        job["spent"] = job["budget"]
        self.assertEqual(transition(job, "running", principal="runner", capabilities=["transition"], current_state=self.current(job))["reason"], "budget_exhausted")

    def test_kill_switch_requires_authorized_capability(self):
        job = self.job("running")
        denied = transition(job, "cancelled", principal="operator", capabilities=["transition"], current_state=self.current(job), kill_switch=True)
        self.assertEqual(denied["reason"], "kill_unauthorized")
        allowed = transition(job, "cancelled", principal="operator", capabilities=["kill"], current_state=self.current(job), kill_switch=True)
        self.assertEqual(allowed["state"], "cancelled")

    def test_identity_and_capabilities_cannot_hide_in_job_payload(self):
        job = {**self.job(), "principal": "attacker", "capabilities": ["approve"]}
        with self.assertRaises(ValueError):
            transition(job, "approved", principal="attacker", capabilities=[], current_state={"id": "job-1", "version": 1, "state": "pending"}, approval=self.approval(self.job()))

    def test_json_payload_cannot_supply_its_own_authorization(self):
        job = self.job()
        payload = {
            "job": job,
            "target": "approved",
            "principal": "attacker",
            "capabilities": ["approve", "kill"],
            "current_state": self.current(job),
            "approval": {**self.approval(job), "approved_by": "attacker"},
            "kill_switch": True,
        }
        with self.assertRaises(ValueError):
            run(payload)
        completed = subprocess.run(
            [sys.executable, "-m", "automation_control_plane.cli"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn('"authorized"', completed.stdout)

    def test_json_boundary_is_explicitly_simulation_only(self):
        result = run({"job": self.job(), "target": "approved"})
        self.assertEqual(result["status"], "simulation_only")
        self.assertEqual(result["authorization"], "unverified")
        self.assertTrue(result["would_transition"])
        self.assertNotIn("approved_by", result)


if __name__ == "__main__":
    unittest.main()
