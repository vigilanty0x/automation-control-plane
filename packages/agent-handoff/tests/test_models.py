import json
import unittest

from fixtures import handoff_dict
from agent_handoff.models import ContractError, Handoff, digest


class ContractTests(unittest.TestCase):
    def test_round_trip_and_digest(self):
        first = Handoff.from_dict(handoff_dict())
        second = Handoff.from_json(json.dumps(first.to_dict()))
        self.assertEqual(first, second)
        self.assertEqual(first.logical_sha256, second.logical_sha256)

    def test_digest_is_mapping_order_independent(self):
        raw = handoff_dict()
        reversed_raw = dict(reversed(list(raw.items())))
        self.assertEqual(Handoff.from_dict(raw).logical_sha256, Handoff.from_dict(reversed_raw).logical_sha256)

    def test_schema_is_exact(self):
        raw = handoff_dict(); raw["schema_version"] = "2.0"
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_unknown_field_fails(self):
        raw = handoff_dict(); raw["hidden"] = True
        with self.assertRaisesRegex(ContractError, "unknown"):
            Handoff.from_dict(raw)

    def test_state_is_bounded(self):
        raw = handoff_dict(); raw["state"] = "success"
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_sequence_is_bounded(self):
        for value in (-1, 1_000_001, "1"):
            raw = handoff_dict(); raw["sequence"] = value
            with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_timestamp_requires_timezone(self):
        raw = handoff_dict(); raw["created_at"] = "2026-01-01T00:00:00"
        with self.assertRaisesRegex(ContractError, "timezone"):
            Handoff.from_dict(raw)

    def test_timestamp_normalizes_utc(self):
        raw = handoff_dict(); raw["created_at"] = "2026-01-01T02:00:00+02:00"
        self.assertEqual(Handoff.from_dict(raw).created_at, "2026-01-01T00:00:00Z")

    def test_paths_are_sorted_and_deduplicated(self):
        raw = handoff_dict(); raw["path_scope"] = ["z.py", "a.py", "z.py"]
        self.assertEqual(Handoff.from_dict(raw).path_scope, ("a.py", "z.py"))

    def test_absolute_path_is_rejected(self):
        raw = handoff_dict(); raw["path_scope"] = ["/etc/passwd"]
        with self.assertRaisesRegex(ContractError, "safe relative"):
            Handoff.from_dict(raw)

    def test_parent_traversal_is_rejected(self):
        raw = handoff_dict(); raw["path_scope"] = ["src/../secret"]
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_limits_are_bounded_integers(self):
        raw = handoff_dict(); raw["limits"] = {"retries": -1}
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_done_requires_evidence(self):
        raw = handoff_dict(); raw["evidence"] = []
        with self.assertRaisesRegex(ContractError, "requires machine-readable evidence"):
            Handoff.from_dict(raw)

    def test_done_requires_all_criteria(self):
        raw = handoff_dict(); raw["criteria"][0]["met"] = False
        with self.assertRaisesRegex(ContractError, "all criteria"):
            Handoff.from_dict(raw)

    def test_waiting_preserves_unmet_criteria(self):
        raw = handoff_dict(); raw["state"] = "waiting"; raw["criteria"][0]["met"] = False
        self.assertEqual(Handoff.from_dict(raw).state, "waiting")

    def test_done_rejects_high_blocker(self):
        raw = handoff_dict(); raw["open_items"] = [{"item_id":"block","severity":"high","kind":"blocker","description":"Needs review"}]
        with self.assertRaisesRegex(ContractError, "cannot hide"):
            Handoff.from_dict(raw)

    def test_done_can_keep_low_risk_visible(self):
        raw = handoff_dict(); raw["open_items"] = [{"item_id":"risk","severity":"low","kind":"risk","description":"Minor limitation"}]
        self.assertEqual(len(Handoff.from_dict(raw).open_items), 1)

    def test_duplicate_criterion_ids_fail(self):
        raw = handoff_dict(); raw["criteria"].append(dict(raw["criteria"][0]))
        with self.assertRaisesRegex(ContractError, "unique"):
            Handoff.from_dict(raw)

    def test_duplicate_evidence_ids_fail(self):
        raw = handoff_dict(); raw["evidence"].append(dict(raw["evidence"][0]))
        with self.assertRaisesRegex(ContractError, "unique"):
            Handoff.from_dict(raw)

    def test_invalid_evidence_kind_fails(self):
        raw = handoff_dict(); raw["evidence"][0]["kind"] = "claim"
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_invalid_evidence_sha_fails(self):
        raw = handoff_dict(); raw["evidence"][0]["sha256"] = "bad"
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            Handoff.from_dict(raw)

    def test_invalid_open_item_kind_fails(self):
        raw = handoff_dict(); raw["open_items"] = [{"item_id":"x","severity":"low","kind":"note","description":"x"}]
        with self.assertRaises(ContractError): Handoff.from_dict(raw)

    def test_invalid_json_is_bounded(self):
        with self.assertRaisesRegex(ContractError, "invalid JSON"):
            Handoff.from_json("{")

    def test_digest_known_shape(self):
        self.assertRegex(digest({"a": 1}), r"^[0-9a-f]{64}$")

