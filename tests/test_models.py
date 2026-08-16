import copy
import unittest

from automation_control_plane.models import ModelError, WorkflowDefinition, canonical_json, parse_json
from tests.support import step, workflow


class WorkflowModelTests(unittest.TestCase):
    def test_round_trip_and_digest_are_deterministic(self):
        first = WorkflowDefinition.from_dict(workflow())
        second = WorkflowDefinition.from_json(canonical_json(first.to_dict()))
        self.assertEqual(first, second)
        self.assertEqual(first.digest, second.digest)

    def test_unknown_fields_are_rejected_at_every_boundary(self):
        value = workflow()
        value["typo"] = True
        with self.assertRaisesRegex(ModelError, "unknown"):
            WorkflowDefinition.from_dict(value)
        value = workflow()
        value["steps"][0]["typo"] = True
        with self.assertRaisesRegex(ModelError, "unknown"):
            WorkflowDefinition.from_dict(value)

    def test_unknown_dependency_is_rejected(self):
        with self.assertRaisesRegex(ModelError, "unknown dependencies"):
            WorkflowDefinition.from_dict(workflow(steps=[step(depends_on=["missing"])]))

    def test_cycle_is_rejected(self):
        steps = [step("a", depends_on=["b"]), step("b", depends_on=["a"])]
        with self.assertRaisesRegex(ModelError, "cycle"):
            WorkflowDefinition.from_dict(workflow(steps=steps))

    def test_duplicate_steps_are_rejected(self):
        with self.assertRaisesRegex(ModelError, "unique"):
            WorkflowDefinition.from_dict(workflow(steps=[step("a"), step("a")]))

    def test_budget_cannot_be_lower_than_estimates(self):
        with self.assertRaisesRegex(ModelError, "exceeds workflow budget"):
            WorkflowDefinition.from_dict(workflow(steps=[step(estimated_cost=2)], budget=1))

    def test_floating_point_json_is_rejected(self):
        value = workflow()
        value["steps"][0]["input"] = {"ambiguous": 0.1}
        with self.assertRaisesRegex(ModelError, "floating-point"):
            WorkflowDefinition.from_dict(value)

    def test_trigger_shapes_are_strict(self):
        bad = workflow(triggers=[{"type": "manual", "event": "extra"}])
        with self.assertRaises(ModelError):
            WorkflowDefinition.from_dict(bad)
        good = workflow(triggers=[{"type": "webhook", "event": "release.requested"}, {"type": "scheduled", "interval_seconds": 60}])
        self.assertEqual(len(WorkflowDefinition.from_dict(good).triggers), 2)

    def test_schema_version_is_explicit(self):
        value = workflow()
        value["schema_version"] = "2.0"
        with self.assertRaisesRegex(ModelError, "unsupported"):
            WorkflowDefinition.from_dict(value)

    def test_digest_changes_with_semantics(self):
        original = WorkflowDefinition.from_dict(workflow())
        changed_value = copy.deepcopy(workflow())
        changed_value["description"] = "Changed."
        changed = WorkflowDefinition.from_dict(changed_value)
        self.assertNotEqual(original.digest, changed.digest)

    def test_model_owns_a_deeply_immutable_input_snapshot(self):
        value = workflow(); value["steps"][0]["input"] = {"nested": [{"value": 1}]}
        model = WorkflowDefinition.from_dict(value)
        value["steps"][0]["input"]["nested"][0]["value"] = 99
        self.assertEqual(model.to_dict()["steps"][0]["input"]["nested"][0]["value"], 1)
        with self.assertRaises(TypeError): model.steps[0].input["new"] = True
        with self.assertRaises(AttributeError): model.steps[0].input["nested"].append("mutation")

    def test_self_dependency_is_rejected(self):
        with self.assertRaisesRegex(ModelError, "cannot include"):
            WorkflowDefinition.from_dict(workflow(steps=[step("a", depends_on=["a"])]))

    def test_duplicate_json_members_are_rejected_at_every_depth(self):
        value = canonical_json(workflow())
        duplicate_top_level = value.replace(
            '"workflow_id":"test-flow"',
            '"workflow_id":"first","workflow_id":"test-flow"',
        )
        with self.assertRaisesRegex(ModelError, "duplicate JSON object key: workflow_id"):
            WorkflowDefinition.from_json(duplicate_top_level)

        duplicate_nested = value.replace(
            '"input":{}',
            '"input":{"actual":"first","actual":"second"}',
        )
        with self.assertRaisesRegex(ModelError, "duplicate JSON object key: actual"):
            WorkflowDefinition.from_json(duplicate_nested)

    def test_nonstandard_json_constants_are_rejected(self):
        for text in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(text=text), self.assertRaisesRegex(ModelError, "non-standard"):
                parse_json(text)

    def test_deep_json_fails_as_a_bounded_model_error(self):
        deeply_nested = "[" * 2_000 + "0" + "]" * 2_000
        with self.assertRaisesRegex(ModelError, "nesting|deeply nested"):
            parse_json(deeply_nested)
        python_value = 0
        for _ in range(2_000):
            python_value = [python_value]
        with self.assertRaisesRegex(ModelError, "deeply nested"):
            canonical_json(python_value)

    def test_python_mapping_with_mixed_unknown_keys_fails_as_model_error(self):
        value = workflow(); value[7] = "invalid"; value["typo"] = True
        with self.assertRaisesRegex(ModelError, "keys must be strings"):
            WorkflowDefinition.from_dict(value)


if __name__ == "__main__":
    unittest.main()
