import unittest

from agent_inbox.contract import (
    AgentProfile, ArtifactEvidence, CommitEvidence, CompletionEvidence, ContractError,
    MissionSpec, TestEvidence, canonical_json, sha256_json,
)
from helpers import artifact, evidence, profile, spec


class MissionSpecTests(unittest.TestCase):
    def test_round_trip(self):
        item = spec(); self.assertEqual(MissionSpec.from_dict(item.to_dict()), item)
    def test_logical_sha_reproducible(self): self.assertEqual(spec().logical_sha256, spec().logical_sha256)
    def test_payload_order_does_not_change_sha(self): self.assertEqual(spec(payload={"a": 1, "b": 2}).logical_sha256, spec(payload={"b": 2, "a": 1}).logical_sha256)
    def test_key_required(self):
        with self.assertRaises(ContractError): spec(idempotency_key=" ")
    def test_title_required(self):
        with self.assertRaises(ContractError): spec(title="")
    def test_payload_must_be_object(self):
        with self.assertRaisesRegex(ContractError, "object"): spec(payload=[])
    def test_payload_must_be_json(self):
        with self.assertRaisesRegex(ContractError, "serializable"): spec(payload={"x": object()})
    def test_payload_bound(self):
        with self.assertRaisesRegex(ContractError, "exceeds"): spec(payload={"x": "a" * 100_000})
    def test_priority_low_bound(self):
        with self.assertRaises(ContractError): spec(priority=-1)
    def test_priority_high_bound(self):
        with self.assertRaises(ContractError): spec(priority=101)
    def test_priority_bool_rejected(self):
        with self.assertRaises(ContractError): spec(priority=True)
    def test_retry_bound(self):
        with self.assertRaises(ContractError): spec(max_retries=21)
    def test_duplicate_requirements_rejected(self):
        with self.assertRaisesRegex(ContractError, "unique"): spec(required_capabilities=("a", "a"))
    def test_requirements_sorted(self): self.assertEqual(spec(required_capabilities=("z", "a")).required_capabilities, ("a", "z"))
    def test_from_dict_rejects_string_requirements(self):
        value = spec().to_dict(); value["required_capabilities"] = "python"
        with self.assertRaises(ContractError): MissionSpec.from_dict(value)


class AgentProfileTests(unittest.TestCase):
    def test_round_trip(self):
        item = profile(); self.assertEqual(AgentProfile.from_dict(item.to_dict()), item)
    def test_empty_ownership_rejected(self):
        with self.assertRaises(ContractError): profile(ownership=())
    def test_running_low_bound(self):
        with self.assertRaises(ContractError): profile(max_running=0)
    def test_running_high_bound(self):
        with self.assertRaises(ContractError): profile(max_running=101)
    def test_lease_low_bound(self):
        with self.assertRaises(ContractError): profile(max_lease_seconds=0)
    def test_lease_high_bound(self):
        with self.assertRaises(ContractError): profile(max_lease_seconds=86_401)
    def test_active_boolean(self):
        with self.assertRaises(ContractError): profile(active=1)
    def test_capabilities_sorted(self): self.assertEqual(profile(capabilities=("z", "a")).capabilities, ("a", "z"))
    def test_from_dict_rejects_string_ownership(self):
        value = profile().to_dict(); value["ownership"] = "demo"
        with self.assertRaises(ContractError): AgentProfile.from_dict(value)


class EvidenceTests(unittest.TestCase):
    def test_commit_sha_required(self):
        with self.assertRaises(ContractError): CommitEvidence("bad", "repo")
    def test_artifact_sha_required(self):
        with self.assertRaises(ContractError): ArtifactEvidence("x", "a" * 40, "x")
    def test_artifact_parent_path_rejected(self):
        with self.assertRaises(ContractError): ArtifactEvidence("x", "a" * 64, "../x")
    def test_test_outcome_rejected(self):
        with self.assertRaises(ContractError): TestEvidence("x", "unknown", "cmd")
    def test_passed_test_and_commit_sufficient(self): self.assertTrue(evidence().sufficient)
    def test_passed_test_and_artifact_sufficient(self): self.assertTrue(evidence(commits=(), artifacts=(artifact(),)).sufficient)
    def test_summary_only_insufficient(self): self.assertFalse(CompletionEvidence("summary").sufficient)
    def test_commit_without_test_insufficient(self): self.assertFalse(evidence(tests=()).sufficient)
    def test_failed_test_insufficient(self): self.assertFalse(evidence(tests=(TestEvidence("x", "failed", "cmd"),)).sufficient)
    def test_skipped_test_insufficient(self): self.assertFalse(evidence(tests=(TestEvidence("x", "skipped", "cmd"),)).sufficient)
    def test_round_trip(self):
        item = evidence(artifacts=(artifact(),)); self.assertEqual(CompletionEvidence.from_dict(item.to_dict()), item)
    def test_evidence_sha_reproducible(self): self.assertEqual(evidence().sha256, evidence().sha256)
    def test_from_dict_rejects_non_object_item(self):
        value = evidence().to_dict(); value["tests"].append("bad")
        with self.assertRaises(ContractError): CompletionEvidence.from_dict(value)
    def test_canonical_json(self): self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')
    def test_json_sha_order_independent(self): self.assertEqual(sha256_json({"a": 1, "b": 2}), sha256_json({"b": 2, "a": 1}))


if __name__ == "__main__": unittest.main()
