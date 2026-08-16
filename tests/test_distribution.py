from __future__ import annotations

import json
import re
import tempfile
import tomllib
import unittest
from importlib import resources
from pathlib import Path

from apprentice_ai.learning import apply_answer, discover_routine, generate_question, segment_sessions
from apprentice_ai.privacy import PrivacyGuard
from apprentice_ai.skills import compile_skill
from apprentice_ai.store import EventStore
from apprentice_ai.synthetic import seed_synthetic_office


ROOT = Path(__file__).parents[1]
SCHEMA_ROOT = ROOT / "src" / "apprentice_ai" / "schemas"


def _schema_matches(value, schema: dict) -> bool:
    try:
        _assert_schema(value, schema)
    except AssertionError:
        return False
    return True


def _assert_schema(value, schema: dict, path: str = "$") -> None:
    if "$ref" in schema:
        referenced = json.loads((SCHEMA_ROOT / schema["$ref"]).read_text(encoding="utf-8"))
        _assert_schema(value, referenced, path)
        return
    expected_type = schema.get("type")
    if expected_type is not None:
        names = expected_type if isinstance(expected_type, list) else [expected_type]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "boolean": lambda item: type(item) is bool,
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "null": lambda item: item is None,
        }
        assert any(checks[name](value) for name in names), f"{path}: expected {names}"
    if "const" in schema:
        assert value == schema["const"], f"{path}: const mismatch"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: enum mismatch"
    if isinstance(value, str):
        if "pattern" in schema:
            assert re.search(schema["pattern"], value), f"{path}: pattern mismatch"
        if "maxLength" in schema:
            assert len(value) <= schema["maxLength"], f"{path}: string too long"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        assert value >= schema["minimum"], f"{path}: below minimum"
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0), f"{path}: too few items"
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            assert len(rendered) == len(set(rendered)), f"{path}: duplicate items"
        for index, item in enumerate(value):
            _assert_schema(item, schema.get("items", {}), f"{path}[{index}]")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        assert required <= set(value), f"{path}: missing {sorted(required - set(value))}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            assert not unknown, f"{path}: unknown {sorted(unknown)}"
        for name, item in value.items():
            if name in properties:
                _assert_schema(item, properties[name], f"{path}.{name}")
    for rule in schema.get("allOf", []):
        condition = rule.get("if")
        if condition is None:
            _assert_schema(value, rule, path)
        elif _schema_matches(value, condition):
            _assert_schema(value, rule.get("then", {}), path)


class DistributionContractTests(unittest.TestCase):
    def test_required_project_documents_are_present(self) -> None:
        required = {
            "README.md",
            "LICENSE",
            "NOTICE",
            "ARCHITECTURE.md",
            "SECURITY.md",
            "PRIVACY.md",
            "THREAT_MODEL.md",
            "GOVERNANCE.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "CHANGELOG.md",
            "ROADMAP.md",
            "AGENTS.md",
            "docs/API.md",
            "docs/CLI.md",
            "docs/LEARNPACK.md",
            "docs/ACCEPTANCE.md",
            "docs/BUILD_METRICS.md",
            "docs/AI_ASSISTANCE.md",
        }
        missing = sorted(name for name in required if not (ROOT / name).is_file())
        self.assertEqual(missing, [])
        self.assertTrue(all((ROOT / name).stat().st_size > 20 for name in required))

    def test_public_schemas_are_valid_closed_json_documents(self) -> None:
        schemas = sorted(SCHEMA_ROOT.glob("*.schema.json"))
        self.assertGreaterEqual(len(schemas), 10)
        identifiers = set()
        for path in schemas:
            with self.subTest(schema=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertIs(value.get("additionalProperties"), False)
                self.assertNotIn(value["$id"], identifiers)
                identifiers.add(value["$id"])

    def test_wheel_package_data_is_addressable(self) -> None:
        package = resources.files("apprentice_ai")
        for relative in (
            "py.typed",
            "web/index.html",
            "web/app.css",
            "web/components.css",
            "web/app.js",
            "schemas/event.schema.json",
            "schemas/event-envelope.schema.json",
            "schemas/skill-ir.schema.json",
            "schemas/learnpack.schema.json",
        ):
            with self.subTest(resource=relative):
                self.assertTrue(package.joinpath(*relative.split("/")).is_file())

    def test_runtime_dependency_and_license_metadata_are_explicit(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertNotIn(
            "License :: OSI Approved :: Apache Software License", project["classifiers"]
        )

    def test_real_timeline_envelope_matches_its_public_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with EventStore(Path(temporary) / "apprentice.sqlite") as store:
                profile = store.create_profile("Schema timeline", "pro_schema_timeline")
                session = store.create_session(profile, source="test", session_id="ses_schema")
                stored = store.append_event(
                    profile,
                    session,
                    {
                        "application": {"id": "synthetic-sheet"},
                        "action": {"kind": "task_start"},
                    },
                    PrivacyGuard(),
                )
                self.assertEqual(store.list_events(profile, session_id=session), [stored])
                schema = json.loads(
                    (SCHEMA_ROOT / "event-envelope.schema.json").read_text(encoding="utf-8")
                )
                _assert_schema(stored, schema)

    def test_pre_holdout_candidate_matches_conditional_routine_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with EventStore(Path(temporary) / "apprentice.sqlite") as store:
                profile = store.create_profile("Pre holdout", "pro_pre_holdout")
                traces = (
                    ("D1", "tropical", ("task_start", "correct_humidity", "task_end")),
                    ("D2", "tropical", ("task_start", "correct_humidity", "task_end")),
                    ("D3", "temperate", ("task_start", "task_end")),
                )
                for demo_id, climate, actions in traces:
                    session = store.create_session(
                        profile,
                        source="test",
                        metadata={
                            "demo_id": demo_id,
                            "climate": climate,
                            "split": "induction",
                            "goal": "normalize_lab_export",
                            "effect": "normalized_export_saved",
                            "synthetic": True,
                        },
                        session_id=f"ses_pre_{demo_id}",
                    )
                    for index, action in enumerate(actions, start=1):
                        store.append_event(
                            profile,
                            session,
                            {
                                "event_id": f"evt_pre_{demo_id}_{index}",
                                "timestamp": f"2026-08-16T10:0{index}:00Z",
                                "source": "test",
                                "application": {"id": "synthetic-sheet"},
                                "action": {"kind": action},
                            },
                            PrivacyGuard(),
                        )
                    store.end_session(profile, session)
                segment_sessions(store, profile)
                routine = discover_routine(store, profile)
                self.assertEqual(routine["status"], "explained")
                self.assertEqual(routine["holdout_ids"], [])
                schema = json.loads(
                    (SCHEMA_ROOT / "routine.schema.json").read_text(encoding="utf-8")
                )
                _assert_schema(routine, schema)
                question = generate_question(store, profile, routine["routine_id"])
                question_schema = json.loads(
                    (SCHEMA_ROOT / "question.schema.json").read_text(encoding="utf-8")
                )
                _assert_schema(question, question_schema)

    def test_real_invalidated_skill_matches_stale_lifecycle_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with EventStore(Path(temporary) / "apprentice.sqlite") as store:
                profile = store.create_profile("Stale schema", "pro_stale_schema")
                seed_synthetic_office(store, profile)
                segment_sessions(store, profile)
                routine = discover_routine(store, profile)
                question = generate_question(store, profile, routine["routine_id"])
                apply_answer(store, profile, question["id"], "yes", synthetic=True)
                compile_skill(store, profile, routine["routine_id"])
                store.invalidate_by_evidence(profile, routine["induction_ids"][0])
                stale = store.list_skills(profile, include_stale=True)[0]
                self.assertEqual(stale["lifecycle"]["status"], "stale")
                schema = json.loads(
                    (SCHEMA_ROOT / "skill-ir.schema.json").read_text(encoding="utf-8")
                )
                _assert_schema(stale, schema)


if __name__ == "__main__":
    unittest.main()
