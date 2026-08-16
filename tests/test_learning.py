from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from apprentice_ai.errors import IntegrityError
from apprentice_ai.learning import apply_answer, discover_routine, generate_question, segment_sessions
from apprentice_ai.privacy import PrivacyGuard
from apprentice_ai.skills import compile_skill, preview_skill, preview_stored_skill
from apprentice_ai.store import EventStore
from apprentice_ai.synthetic import seed_synthetic_office


class LearningPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "apprentice.sqlite")
        self.profile = self.store.create_profile("Synthetic", "pro_synthetic")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def run_until_question(self):
        receipt = seed_synthetic_office(self.store, self.profile, PrivacyGuard())
        episodes = segment_sessions(self.store, self.profile)
        routine = discover_routine(self.store, self.profile)
        question = generate_question(self.store, self.profile, routine["routine_id"])
        return receipt, episodes, routine, question

    def test_d1_d5_vertical_slice_detects_branch_and_holdout(self) -> None:
        receipt, episodes, routine, question = self.run_until_question()
        self.assertEqual(receipt["demonstrations"], 5)
        self.assertEqual(receipt["events_blocked"], 1)
        self.assertGreaterEqual(receipt["events_redacted"], 1)
        self.assertEqual(len(episodes), 5)
        self.assertTrue(all(item["status"] == "complete" for item in episodes))
        self.assertEqual(len(routine["induction_ids"]), 3)
        self.assertEqual(len(routine["holdout_ids"]), 2)
        branch = routine["branches"][0]
        self.assertEqual(branch["step"], "correct_humidity")
        self.assertEqual(branch["when"], {"field": "climate", "operator": "eq", "value": "tropical"})
        self.assertEqual(routine["scores"]["holdout_pass_rate"], 1.0)
        self.assertEqual(question["routine_id"], routine["routine_id"])
        self.assertIn("climate = tropical", question["short_text"])

    def test_unknown_answer_does_not_confirm_or_create_memory(self) -> None:
        _, _, routine, question = self.run_until_question()
        result = apply_answer(self.store, self.profile, question["id"], "unknown")
        self.assertEqual(result["status"], "explained")
        self.assertIsNone(result["memory_id"])
        self.assertEqual(self.store.list_memories(self.profile), [])
        with self.assertRaises(IntegrityError):
            compile_skill(self.store, self.profile, routine["routine_id"])

    def test_yes_answer_compiles_preview_only_skill(self) -> None:
        _, _, routine, question = self.run_until_question()
        answer = apply_answer(self.store, self.profile, question["id"], "yes")
        self.assertTrue(answer["holdout_passed"])
        self.assertEqual(answer["status"], "confirmed")
        skill = compile_skill(self.store, self.profile, routine["routine_id"])
        preview = preview_skill(skill, {"source_dataset": "fixture://D6"})
        self.assertFalse(preview["execution_allowed"])
        self.assertFalse(preview["data_leaves_machine"])
        self.assertEqual(preview["network"], "deny")
        humidity = next(step for step in skill["steps"] if step["action"].endswith("correct_humidity"))
        self.assertEqual(humidity["when"]["value"], "tropical")

    def test_evidence_invalidation_stales_routine_and_skill(self) -> None:
        _, _, routine, question = self.run_until_question()
        apply_answer(self.store, self.profile, question["id"], "yes", synthetic=True)
        skill = compile_skill(self.store, self.profile, routine["routine_id"])
        evidence_ref = routine["induction_ids"][0]
        self.assertEqual(self.store.invalidate_by_evidence(self.profile, evidence_ref), 1)
        self.assertEqual(self.store.get_routine(self.profile, routine["routine_id"])["status"], "stale")
        self.assertEqual(self.store.list_skills(self.profile), [])
        stale = self.store.list_skills(self.profile, include_stale=True)[0]
        self.assertEqual(stale["lifecycle"]["status"], "stale")
        with self.assertRaisesRegex(IntegrityError, "stale"):
            preview_skill(stale)
        with self.assertRaisesRegex(IntegrityError, "stale"):
            preview_stored_skill(
                self.store, self.profile, skill["skill_id"], skill["version"], {}
            )

    def test_segmentation_is_idempotent(self) -> None:
        seed_synthetic_office(self.store, self.profile)
        self.assertEqual(len(segment_sessions(self.store, self.profile)), 5)
        self.assertEqual(segment_sessions(self.store, self.profile), [])

    def test_unknown_goal_abstains_instead_of_claiming_demo_intent(self) -> None:
        session = self.store.create_session(
            self.profile,
            mode="import",
            source="test",
            metadata={"synthetic": False},
            session_id="ses_unknown_goal",
        )
        for index, action in enumerate(("task_start", "open_file", "task_end"), start=1):
            self.store.append_event(
                self.profile,
                session,
                {
                    "event_id": f"evt_unknown_{index}",
                    "timestamp": f"2026-08-16T11:0{index}:00Z",
                    "application": {"id": "unknown-app"},
                    "action": {"kind": action},
                },
                PrivacyGuard(),
            )
        self.store.end_session(self.profile, session)
        episode = segment_sessions(self.store, self.profile)[0]
        self.assertEqual(episode["goal_hypotheses"][0]["goal"], "unknown")
        self.assertTrue(episode["segmentation"]["abstained"])

    def test_active_session_with_boundaries_is_not_promoted_to_episode(self) -> None:
        session = self.store.create_session(
            self.profile,
            mode="import",
            source="test",
            metadata={
                "goal": "normalize_lab_export",
                "effect": "normalized_export_saved",
                "split": "induction",
            },
            session_id="ses_active_boundaries",
        )
        for index, action in enumerate(("task_start", "open_file", "task_end"), start=1):
            self.store.append_event(
                self.profile,
                session,
                {
                    "event_id": f"evt_active_{index}",
                    "timestamp": f"2026-08-16T13:0{index}:00Z",
                    "application": {"id": "synthetic-sheet"},
                    "action": {"kind": action},
                },
                PrivacyGuard(),
            )
        self.assertEqual(segment_sessions(self.store, self.profile), [])

    def test_different_effect_is_excluded_from_routine(self) -> None:
        seed_synthetic_office(self.store, self.profile)
        noise = self.store.create_session(
            self.profile,
            mode="synthetic",
            source="test",
            metadata={
                "demo_id": "NOISE",
                "climate": "tropical",
                "split": "induction",
                "goal": "normalize_lab_export",
                "effect": "report_only_not_saved",
                "synthetic": True,
            },
            session_id="ses_noise_effect",
        )
        for index, action in enumerate(("task_start", "open_file", "normalize_units", "task_end"), start=1):
            self.store.append_event(
                self.profile,
                noise,
                {
                    "event_id": f"evt_noise_{index}",
                    "timestamp": f"2026-08-16T12:0{index}:00Z",
                    "application": {"id": "synthetic-sheet"},
                    "action": {"kind": action},
                },
                PrivacyGuard(),
            )
        self.store.end_session(self.profile, noise)
        episodes = segment_sessions(self.store, self.profile)
        noise_episode = next(item for item in episodes if item["session_id"] == noise)
        routine = discover_routine(self.store, self.profile)
        self.assertNotIn(noise_episode["episode_id"], routine["occurrence_ids"])
        self.assertIn(noise_episode["episode_id"], routine["excluded_episode_ids"])
        self.assertEqual(routine["effect"], "normalized_export_saved")

    def test_question_budget_is_scoped_to_calendar_day(self) -> None:
        seed_synthetic_office(self.store, self.profile)
        segment_sessions(self.store, self.profile)
        routine = discover_routine(self.store, self.profile)
        today = datetime.now(UTC)
        generate_question(self.store, self.profile, routine["routine_id"], daily_budget=1, now=today)
        alternative = dict(routine)
        alternative["routine_id"] = "rou_budget_other"
        self.store.put_routine(self.profile, alternative)
        with self.assertRaisesRegex(IntegrityError, "budget"):
            generate_question(
                self.store, self.profile, alternative["routine_id"], daily_budget=1, now=today
            )

    def test_question_budget_bounds_are_validated_before_state_lookup(self) -> None:
        for budget in (0, -1, 101, True):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(Exception, "between 1 and 100"):
                    generate_question(self.store, self.profile, "rou_missing", daily_budget=budget)

    def test_question_snooze_resume_dismiss_transitions(self) -> None:
        _, _, routine, question = self.run_until_question()
        future = "2099-01-01T00:00:00Z"
        snoozed = self.store.transition_question(
            self.profile, question["id"], "snoozed", snoozed_until=future
        )
        self.assertEqual(snoozed["status"], "snoozed")
        queued = self.store.transition_question(self.profile, question["id"], "queued")
        self.assertEqual(queued["status"], "queued")
        dismissed = self.store.transition_question(self.profile, question["id"], "dismissed")
        self.assertEqual(dismissed["status"], "dismissed")
        with self.assertRaises(IntegrityError):
            self.store.transition_question(self.profile, question["id"], "queued")

    def test_compiler_rejects_unreviewed_routine_template(self) -> None:
        alternative = {
            "routine_id": "rou_alternative",
            "intent": "publish_report",
            "effect": "report_published",
            "title": "Publish report",
            "status": "confirmed",
            "prototype_steps": ["open_file", "publish_report"],
            "branches": [],
            "holdout_evaluation": [{"episode_id": "epi_alt", "passed": True, "checks": []}],
            "evidence_refs": ["epi_alt"],
        }
        self.store.put_routine(self.profile, alternative)
        with self.assertRaisesRegex(IntegrityError, "template") as context:
            compile_skill(self.store, self.profile, "rou_alternative")
        self.assertEqual(context.exception.code, "UNSUPPORTED_ROUTINE_TEMPLATE")

    def test_forged_confirmed_reference_state_without_evidence_cannot_compile(self) -> None:
        forged = {
            "routine_id": "rou_forged_reference",
            "intent": "normalize_lab_export",
            "effect": "normalized_export_saved",
            "title": "Forged",
            "status": "confirmed",
            "prototype_steps": ["open_file", "correct_humidity", "save_output"],
            "branches": [
                {
                    "branch_id": "branch_1",
                    "step": "correct_humidity",
                    "when": {"field": "climate", "operator": "eq", "value": "tropical"},
                }
            ],
            "induction_ids": ["epi_fake_1", "epi_fake_2", "epi_fake_3"],
            "holdout_ids": ["epi_fake_4", "epi_fake_5"],
            "holdout_evaluation": [],
            "evidence_refs": [],
        }
        self.store.put_routine(self.profile, forged)
        with self.assertRaisesRegex(IntegrityError, "missing") as context:
            compile_skill(self.store, self.profile, "rou_forged_reference")
        self.assertEqual(context.exception.code, "EVIDENCE_INVALID")

    def test_memory_assertion_has_explicit_append_only_version(self) -> None:
        _, _, _, question = self.run_until_question()
        apply_answer(self.store, self.profile, question["id"], "yes", synthetic=True)
        memory = self.store.list_memories(self.profile)[0]
        self.assertEqual(memory["version"], 1)
        self.assertIsNone(memory["supersedes"])

    def test_public_question_answer_compile_flow_is_idempotent(self) -> None:
        _, _, routine, question = self.run_until_question()
        duplicate = generate_question(self.store, self.profile, routine["routine_id"])
        self.assertEqual(duplicate["id"], question["id"])
        first_answer = apply_answer(
            self.store, self.profile, question["id"], "yes", synthetic=True
        )
        first_skill = compile_skill(self.store, self.profile, routine["routine_id"])
        second_answer = apply_answer(
            self.store, self.profile, question["id"], "yes", synthetic=True
        )
        second_skill = compile_skill(self.store, self.profile, routine["routine_id"])
        self.assertEqual(first_answer["answer_id"], second_answer["answer_id"])
        self.assertTrue(second_answer["replayed"])
        self.assertEqual(first_skill, second_skill)
        self.assertEqual(len(self.store.list_questions(self.profile)), 1)
        self.assertEqual(len(self.store.list_memories(self.profile)), 1)
        self.assertEqual(
            self.store.get_routine(self.profile, routine["routine_id"])["status"],
            "compilable",
        )

    def test_sensitive_explanation_is_canonical_before_idempotency_comparison(self) -> None:
        _, _, _, question = self.run_until_question()
        secret = "api_key=NEVER-PERSIST-ANSWER-12345"
        first = apply_answer(
            self.store,
            self.profile,
            question["id"],
            "unknown",
            explanation=secret,
        )
        second = apply_answer(
            self.store,
            self.profile,
            question["id"],
            "unknown",
            explanation=secret,
        )
        self.assertFalse(first.get("replayed", False))
        self.assertTrue(second["replayed"])
        stored = self.store.get_answer(self.profile, question["id"])
        self.assertNotIn("NEVER-PERSIST-ANSWER", stored["explanation"])
        self.store.connection.execute("PRAGMA wal_checkpoint(FULL)")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.store.path}{suffix}")
            if candidate.exists():
                self.assertNotIn(b"NEVER-PERSIST-ANSWER", candidate.read_bytes())


if __name__ == "__main__":
    unittest.main()
