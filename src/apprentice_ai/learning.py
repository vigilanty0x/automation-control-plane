"""Deterministic, inspectable baseline for episodes, routines and questions."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .contracts import new_id, utc_now
from .errors import IntegrityError, ValidationError
from .store import EventStore
from .strictjson import canonical_bytes
from .synthetic import ACTION_LABELS


def segment_sessions(store: EventStore, profile_id: str) -> list[dict[str, Any]]:
    existing_sessions = {item["session_id"] for item in store.list_episodes(profile_id)}
    created: list[dict[str, Any]] = []
    for session in store.list_sessions(profile_id):
        if session["session_id"] in existing_sessions:
            continue
        if session["status"] == "active":
            continue
        chain = store.verify_chain(profile_id, session["session_id"])
        if not chain["sealed"]:
            continue
        events = store.list_events(profile_id, session_id=session["session_id"])
        if not events:
            continue
        action_kinds = [str(event.get("action", {}).get("kind", "unknown")) for event in events]
        has_start = "task_start" in action_kinds
        has_end = "task_end" in action_kinds
        meaningful = [item for item in action_kinds if item not in {"task_start", "task_end"}]
        metadata = dict(session.get("metadata", {}))
        goal = metadata.get("goal") if isinstance(metadata.get("goal"), str) else None
        effect = metadata.get("effect") if isinstance(metadata.get("effect"), str) else None
        episode = {
            "episode_id": new_id("epi"),
            "session_id": session["session_id"],
            "start_event_id": events[0]["event_id"],
            "end_event_id": events[-1]["event_id"],
            "label_candidates": [
                {"label": goal or "unknown", "score": 0.95 if goal and has_start and has_end else 0.0}
            ],
            "goal_hypotheses": [
                {"goal": goal or "unknown", "score": 0.90 if goal and has_end else 0.0}
            ],
            "effect": effect,
            "actions": meaningful,
            "context": {
                "demo_id": metadata.get("demo_id"),
                "climate": metadata.get("climate"),
                "split": metadata.get("split", "unknown"),
                "synthetic": bool(metadata.get("synthetic", False)),
            },
            "privacy_class": max(
                (str(event.get("privacy", {}).get("classification", "D1")) for event in events),
                default="D1",
            ),
            "evidence_refs": [event["event_id"] for event in events],
            "status": "complete" if has_start and has_end else "incomplete",
            "segmentation": {
                "method": "explicit-boundary-baseline/0.1.0",
                "abstained": not (has_start and has_end and goal and effect),
            },
        }
        store.put_episode(profile_id, session["session_id"], episode)
        created.append(episode)
    return created


def _lcs(left: list[str], right: list[str]) -> list[str]:
    table: list[list[list[str]]] = [[[] for _ in range(len(right) + 1)] for _ in range(len(left) + 1)]
    for i, lvalue in enumerate(left, start=1):
        for j, rvalue in enumerate(right, start=1):
            if lvalue == rvalue:
                table[i][j] = table[i - 1][j - 1] + [lvalue]
            else:
                above = table[i - 1][j]
                prior = table[i][j - 1]
                table[i][j] = above if len(above) >= len(prior) else prior
    return table[-1][-1]


def discover_routine(
    store: EventStore,
    profile_id: str,
    *,
    goal: str | None = None,
    effect: str | None = None,
) -> dict[str, Any]:
    episodes = [item for item in store.list_episodes(profile_id) if item.get("status") == "complete"]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        episode_goal = str(episode.get("goal_hypotheses", [{}])[0].get("goal", "unknown"))
        episode_effect = str(episode.get("effect") or "unknown")
        groups[(episode_goal, episode_effect)].append(episode)
    eligible = {
        key: values
        for key, values in groups.items()
        if len([item for item in values if item.get("context", {}).get("split") == "induction"]) >= 3
        and key != ("unknown", "unknown")
    }
    if goal is not None:
        eligible = {key: values for key, values in eligible.items() if key[0] == goal}
    if effect is not None:
        eligible = {key: values for key, values in eligible.items() if key[1] == effect}
    if not eligible:
        raise ValidationError("no goal/effect group has three complete induction episodes")
    if len(eligible) > 1:
        scopes = ", ".join(f"{key[0]}->{key[1]}" for key in sorted(eligible))
        raise ValidationError(f"multiple routine scopes are eligible; choose one explicitly: {scopes}")
    (routine_goal, routine_effect), episodes = next(iter(eligible.items()))
    induction = [item for item in episodes if item.get("context", {}).get("split") == "induction"]
    holdout = [item for item in episodes if item.get("context", {}).get("split") == "holdout"]
    if len(induction) < 3:
        raise ValidationError("at least three complete induction episodes are required")
    prototype = list(induction[0]["actions"])
    common = list(prototype)
    for episode in induction[1:]:
        common = _lcs(common, list(episode["actions"]))
    all_steps = sorted({step for item in induction for step in item["actions"]})
    branches: list[dict[str, Any]] = []
    for step in all_steps:
        presence = [step in item["actions"] for item in induction]
        if all(presence) or not any(presence):
            continue
        context_keys = sorted(
            set.intersection(*(set(item.get("context", {})) for item in induction))
        )
        for key in context_keys:
            if key in {"demo_id", "split", "synthetic"}:
                continue
            values = sorted({str(item["context"].get(key)) for item in induction})
            if len(values) < 2:
                continue
            for value in values:
                selected = [
                    (str(item["context"].get(key)) == value, step in item["actions"])
                    for item in induction
                ]
                if all(present for matches, present in selected if matches) and all(
                    not present for matches, present in selected if not matches
                ):
                    evidence = [
                        item["episode_id"] for item in induction if step in item["actions"]
                    ]
                    branches.append(
                        {
                            "branch_id": f"branch_{len(branches) + 1}",
                            "step": step,
                            "step_label": ACTION_LABELS.get(step, step),
                            "when": {"field": key, "operator": "eq", "value": value},
                            "support_present": sum(presence),
                            "support_absent": len(presence) - sum(presence),
                            "evidence_refs": evidence,
                        }
                    )
                    break
            if branches and branches[-1]["step"] == step:
                break
    evaluations: list[dict[str, Any]] = []
    for episode in holdout:
        matched = True
        checks: list[dict[str, Any]] = []
        for branch in branches:
            expected = str(episode["context"].get(branch["when"]["field"])) == str(
                branch["when"]["value"]
            )
            observed = branch["step"] in episode["actions"]
            checks.append({"branch_id": branch["branch_id"], "expected": expected, "observed": observed})
            matched = matched and expected == observed
        evaluations.append(
            {
                "episode_id": episode["episode_id"],
                "demo_id": episode.get("context", {}).get("demo_id"),
                "split": episode.get("context", {}).get("split"),
                "passed": matched,
                "checks": checks,
            }
        )
    routine = {
        "routine_id": new_id("rou"),
        "intent": routine_goal,
        "effect": routine_effect,
        "title": routine_goal.replace("_", " ").strip().capitalize(),
        "status": "explained" if branches else "observed",
        "occurrence_ids": [item["episode_id"] for item in episodes],
        "induction_ids": [item["episode_id"] for item in induction],
        "holdout_ids": [item["episode_id"] for item in holdout],
        "prototype_steps": prototype,
        "common_steps": common,
        "branches": branches,
        "holdout_evaluation": evaluations,
        "scores": {
            "occurrences": len(episodes),
            "induction": len(induction),
            "holdout": len(holdout),
            "holdout_pass_rate": (
                sum(1 for item in evaluations if item["passed"]) / len(evaluations)
                if evaluations
                else 0.0
            ),
        },
        "evidence_refs": [item["episode_id"] for item in episodes],
        "excluded_episode_ids": [
            item["episode_id"]
            for key, values in groups.items()
            if key != (routine_goal, routine_effect)
            for item in values
        ],
        "derived_by": {"component": "pattern-miner", "version": "0.1.0", "model": None},
        "created_at": utc_now(),
    }
    store.put_routine(profile_id, routine)
    return routine


def generate_question(
    store: EventStore,
    profile_id: str,
    routine_id: str,
    *,
    daily_budget: int = 3,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(daily_budget, bool)
        or not isinstance(daily_budget, int)
        or not 1 <= daily_budget <= 100
    ):
        raise ValidationError("daily question budget must be between 1 and 100")
    routine = store.get_routine(profile_id, routine_id)
    branches = routine.get("branches", [])
    if not branches:
        raise ValidationError("routine has no ambiguity worth asking about")
    branch = branches[0]
    for item in store.list_questions(profile_id):
        if item.get("routine_id") == routine_id and item.get("branch") == branch:
            return item
    moment = now or datetime.now(UTC)
    day = moment.date()
    existing = []
    for item in store.list_questions(profile_id):
        created = item.get("created_at")
        try:
            created_day = datetime.fromisoformat(str(created).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if created_day == day and item.get("status") != "expired":
            existing.append(item)
    if len(existing) >= daily_budget:
        raise IntegrityError("question interruption budget reached", code="STOP_BUDGET")
    field = branch["when"]["field"]
    value = branch["when"]["value"]
    question = {
        "id": new_id("qst"),
        "routine_id": routine_id,
        "trigger": "branch_condition_from_episode_clusters",
        "short_text": (
            f"L’étape « {branch['step_label']} » est-elle réservée aux cas "
            f"où {field} = {value} ?"
        ),
        "explanation": (
            f"{branch['support_present']} occurrences contiennent cette étape et "
            f"{branch['support_absent']} ne la contiennent pas."
        ),
        "status": "queued",
        "answer_schema": {
            "choices": ["yes", "no", "unknown"],
            "free_text_max_length": 500,
        },
        "evidence_refs": list(branch["evidence_refs"]),
        "expected_utility": {
            "information_gain": 0.9,
            "decision_impact": 0.9,
            "interruption_cost": 0.15,
            "privacy_risk": 0.0,
            "score": 0.75,
        },
        "privacy": {"scope": "local_only", "contains_sensitive_context": False},
        "consequence_preview": {
            "yes": f"Ajoute la condition {field} == {value} à l’étape {branch['step']}.",
            "no": "Conserve la branche comme hypothèse non confirmée.",
            "unknown": "Ne modifie aucune règle et attend davantage de preuves.",
        },
        "branch": branch,
        "created_at": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    store.put_question(profile_id, routine_id, question)
    return question


def apply_answer(
    store: EventStore,
    profile_id: str,
    question_id: str,
    choice: str,
    *,
    explanation: str = "",
    synthetic: bool = False,
) -> dict[str, Any]:
    if not isinstance(choice, str) or choice not in {"yes", "no", "unknown"}:
        raise ValidationError("answer must be yes, no or unknown")
    if not isinstance(explanation, str) or len(explanation) > 500:
        raise ValidationError("answer explanation is too long")
    if type(synthetic) is not bool:
        raise ValidationError("answer synthetic flag must be boolean")
    safe_explanation, _ = store.persistence_guard.scan_text(explanation.strip())
    routine_id, question = store.get_question(profile_id, question_id)
    routine = store.get_routine(profile_id, routine_id)
    if question.get("status") == "answered":
        existing = store.get_answer(profile_id, question_id)
        if (
            existing.get("choice") != choice
            or existing.get("explanation", "") != safe_explanation
            or existing.get("synthetic", False) is not synthetic
        ):
            raise IntegrityError(
                "question already has a different answer", code="ANSWER_CONFLICT"
            )
        memory_id = next(
            (
                memory.get("id")
                for memory in store.list_memories(profile_id)
                if memory.get("provenance", {}).get("answer") == existing.get("answer_id")
            ),
            None,
        )
        return {
            "answer_id": existing["answer_id"],
            "choice": existing["choice"],
            "routine_id": routine_id,
            "memory_id": memory_id,
            "status": routine["status"],
            "holdout_passed": routine.get("answer_outcome") == "confirmed",
            "replayed": True,
        }
    answer_id = new_id("ans")
    answer = {
        "answer_id": answer_id,
        "choice": choice,
        "explanation": safe_explanation,
        "synthetic": synthetic,
        "answered_at": utc_now(),
    }
    result: dict[str, Any] = {
        "answer_id": answer_id,
        "choice": choice,
        "routine_id": routine_id,
        "memory_id": None,
        "status": routine["status"],
    }
    if choice != "yes":
        routine["answer_outcome"] = "not_confirmed"
        store.commit_answer_outcome(profile_id, question_id, answer, routine, None)
        return result
    evaluations = routine.get("holdout_evaluation", [])
    holdout_passed = bool(evaluations) and all(item.get("passed") for item in evaluations)
    branch = question["branch"]
    routine["status"] = "confirmed" if holdout_passed else "explained"
    routine["answer_outcome"] = "confirmed" if holdout_passed else "holdout_failed"
    routine["confirmed_by"] = answer_id
    memory = {
        "id": new_id("mem"),
        "version": 1,
        "supersedes": None,
        "type": "procedural",
        "subject": f"routine.{routine['intent']}",
        "predicate": "applies_when",
        "object": {
            "step": branch["step"],
            "expression": (
                f"{branch['when']['field']} {branch['when']['operator']} "
                f"{branch['when']['value']}"
            ),
        },
        "status": "confirmed" if holdout_passed else "candidate",
        "confidence": {"score": 1.0 if holdout_passed else 0.5, "calibrated": False},
        "scope": {"profile": profile_id, "applications": ["synthetic-lab", "synthetic-sheet"]},
        "provenance": {
            "evidence": list(routine["induction_ids"]) + list(routine["holdout_ids"]),
            "answer": answer_id,
            "derived_by": {"component": "question-engine", "version": "0.1.0", "model": None},
        },
        "privacy": {"class": "D1", "export_policy": "abstract_only"},
        "created_at": utc_now(),
    }
    _, memory_id = store.commit_answer_outcome(profile_id, question_id, answer, routine, memory)
    result.update({"memory_id": memory_id, "status": routine["status"], "holdout_passed": holdout_passed})
    return result


def routine_digest(routine: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(routine)).hexdigest()
