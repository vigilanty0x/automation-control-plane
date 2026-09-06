import json
import unittest

from fixtures import graph, graph_dict
from taskgraph.models import ContractError, Evidence, GraphSpec


class GraphContractTests(unittest.TestCase):
    def test_round_trip_and_digest(self):
        first = graph(); second = GraphSpec.from_json(json.dumps(first.to_dict()))
        self.assertEqual(first, second); self.assertEqual(first.digest, second.digest)

    def test_digest_mapping_order_independent(self):
        raw = graph_dict(); reversed_raw = dict(reversed(list(raw.items())))
        self.assertEqual(GraphSpec.from_dict(raw).digest, GraphSpec.from_dict(reversed_raw).digest)

    def test_topological_order(self):
        self.assertEqual(graph().topological_order(), ("a-contract", "b-build", "c-review"))

    def test_unknown_graph_field_fails(self):
        raw = graph_dict(); raw["secret"] = True
        with self.assertRaisesRegex(ContractError, "unknown"): GraphSpec.from_dict(raw)

    def test_schema_exact(self):
        raw = graph_dict(); raw["schema_version"] = "2.0"
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_semver_required(self):
        raw = graph_dict(); raw["version"] = "latest"
        with self.assertRaisesRegex(ContractError, "semantic"): GraphSpec.from_dict(raw)

    def test_empty_tasks_fail(self):
        raw = graph_dict(); raw["tasks"] = []
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_duplicate_task_ids_fail(self):
        raw = graph_dict(); raw["tasks"].append(dict(raw["tasks"][0]))
        with self.assertRaisesRegex(ContractError, "unique"): GraphSpec.from_dict(raw)

    def test_unknown_dependency_fails(self):
        raw = graph_dict(); raw["tasks"][1]["dependencies"] = ["missing"]
        with self.assertRaisesRegex(ContractError, "unknown dependencies"): GraphSpec.from_dict(raw)

    def test_self_dependency_fails(self):
        raw = graph_dict(); raw["tasks"][0]["dependencies"] = ["a-contract"]
        with self.assertRaisesRegex(ContractError, "itself"): GraphSpec.from_dict(raw)

    def test_cycle_fails(self):
        raw = graph_dict(); raw["tasks"][0]["dependencies"] = ["c-review"]
        with self.assertRaisesRegex(ContractError, "cycle"): GraphSpec.from_dict(raw)

    def test_path_ownership_conflict_fails(self):
        raw = graph_dict(); raw["tasks"][1]["path_scope"] = ["docs/contract.md"]
        with self.assertRaisesRegex(ContractError, "ownership conflict"): GraphSpec.from_dict(raw)

    def test_absolute_path_fails(self):
        raw = graph_dict(); raw["tasks"][0]["path_scope"] = ["/tmp/x"]
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_parent_traversal_fails(self):
        raw = graph_dict(); raw["tasks"][0]["path_scope"] = ["docs/../x"]
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_attempts_bounded(self):
        for value in (0, 11, "2"):
            raw = graph_dict(); raw["tasks"][0]["max_attempts"] = value
            with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_required_evidence_nonempty(self):
        raw = graph_dict(); raw["tasks"][0]["required_evidence"] = []
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_unknown_evidence_kind_fails(self):
        raw = graph_dict(); raw["tasks"][0]["required_evidence"] = ["wish"]
        with self.assertRaises(ContractError): GraphSpec.from_dict(raw)

    def test_invalid_json_bounded(self):
        with self.assertRaisesRegex(ContractError, "invalid JSON"): GraphSpec.from_json("{")


class EvidenceTests(unittest.TestCase):
    def test_round_trip(self):
        raw = {"kind":"test","uri":"artifact://test","sha256":"a"*64}
        self.assertEqual(Evidence.from_dict(raw).to_dict(), raw)

    def test_sha_shape(self):
        with self.assertRaises(ContractError): Evidence.from_dict({"kind":"test","uri":"x","sha256":"bad"})

    def test_unknown_field(self):
        with self.assertRaises(ContractError): Evidence.from_dict({"kind":"test","uri":"x","sha256":"a"*64,"x":1})

