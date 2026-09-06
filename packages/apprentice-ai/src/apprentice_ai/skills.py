"""Skill IR compilation, linting and non-executing previews."""

from __future__ import annotations

from typing import Any

from .contracts import PermissionManifest, SPEC_VERSION, utc_now
from .errors import IntegrityError, NotFoundError, ValidationError
from .privacy import PrivacyGuard
from .store import EventStore

REFERENCE_TEMPLATE = {
    "intent": "normalize_lab_export",
    "effect": "normalized_export_saved",
    "skill_id": "org.apprentice.synthetic.normalize-lab-export",
    "description": "Normalize a synthetic laboratory export and conditionally correct humidity.",
    "input": {
        "name": "source_dataset",
        "type": "synthetic/csv",
        "required_fields": ["sample_id", "temperature", "unit", "climate"],
    },
    "preconditions": [
        {"check": "input.synthetic", "equals": True},
        {"check": "input.schema_valid", "equals": True},
    ],
    "postconditions": [
        {"check": "output.row_count", "equals_ref": "input.row_count"},
        {"check": "output.units_normalized", "equals": True},
    ],
}


def _verify_reference_store_evidence(
    store: EventStore, profile_id: str, routine: dict[str, Any]
) -> None:
    episodes = {item["episode_id"]: item for item in store.list_episodes(profile_id)}
    induction_ids = routine.get("induction_ids")
    holdout_ids = routine.get("holdout_ids")
    if (
        not isinstance(induction_ids, list)
        or not isinstance(holdout_ids, list)
        or len(induction_ids) != 3
        or len(holdout_ids) != 2
        or len(set(induction_ids)) != 3
        or len(set(holdout_ids)) != 2
        or set(induction_ids) & set(holdout_ids)
    ):
        raise IntegrityError("routine induction/holdout partition is invalid", code="EVIDENCE_INVALID")
    if any(identifier not in episodes for identifier in induction_ids + holdout_ids):
        raise IntegrityError("routine references missing profile-scoped episodes", code="EVIDENCE_INVALID")
    induction = [episodes[identifier] for identifier in induction_ids]
    holdout = [episodes[identifier] for identifier in holdout_ids]
    if {item.get("context", {}).get("demo_id") for item in induction} != {"D1", "D2", "D3"}:
        raise IntegrityError("reference induction must be D1-D3", code="EVIDENCE_INVALID")
    if {item.get("context", {}).get("demo_id") for item in holdout} != {"D4", "D5"}:
        raise IntegrityError("reference holdout must be D4-D5", code="EVIDENCE_INVALID")
    if any(item.get("context", {}).get("split") != "induction" for item in induction):
        raise IntegrityError("induction episode split is invalid", code="EVIDENCE_INVALID")
    if any(item.get("context", {}).get("split") != "holdout" for item in holdout):
        raise IntegrityError("holdout episode split is invalid", code="EVIDENCE_INVALID")
    if any(
        item.get("goal_hypotheses", [{}])[0].get("goal") != REFERENCE_TEMPLATE["intent"]
        or item.get("effect") != REFERENCE_TEMPLATE["effect"]
        for item in induction + holdout
    ):
        raise IntegrityError("episode goal/effect evidence differs from template", code="EVIDENCE_INVALID")
    branches = routine.get("branches")
    if not isinstance(branches, list) or len(branches) != 1:
        raise IntegrityError("reference routine requires one proven branch", code="EVIDENCE_INVALID")
    branch = branches[0]
    expected_when = {"field": "climate", "operator": "eq", "value": "tropical"}
    if branch.get("step") != "correct_humidity" or branch.get("when") != expected_when:
        raise IntegrityError("reference branch differs from observed rule", code="EVIDENCE_INVALID")
    recomputed: list[dict[str, Any]] = []
    for episode in holdout:
        expected = episode.get("context", {}).get("climate") == "tropical"
        observed = "correct_humidity" in episode.get("actions", [])
        recomputed.append(
            {
                "episode_id": episode["episode_id"],
                "demo_id": episode.get("context", {}).get("demo_id"),
                "split": "holdout",
                "passed": expected == observed,
                "checks": [
                    {
                        "branch_id": branch.get("branch_id"),
                        "expected": expected,
                        "observed": observed,
                    }
                ],
            }
        )
    if routine.get("holdout_evaluation") != recomputed or not all(item["passed"] for item in recomputed):
        raise IntegrityError("routine holdout evidence does not recompute", code="EVIDENCE_INVALID")
    confirming_answer: dict[str, Any] | None = None
    confirming_question: dict[str, Any] | None = None
    for question in store.list_questions(profile_id):
        if question.get("routine_id") != routine.get("routine_id") or question.get("status") != "answered":
            continue
        try:
            answer = store.get_answer(profile_id, question["id"])
        except NotFoundError:
            continue
        if answer.get("choice") == "yes" and question.get("branch") == branch:
            confirming_answer = answer
            confirming_question = question
            break
    if confirming_answer is None or confirming_question is None:
        raise IntegrityError("routine has no linked affirmative answer", code="EVIDENCE_INVALID")
    required_evidence = set(induction_ids + holdout_ids)
    memories = store.list_memories(profile_id)
    valid_memory = any(
        memory.get("type") == "procedural"
        and memory.get("status") == "confirmed"
        and memory.get("version") == 1
        and memory.get("provenance", {}).get("answer") == confirming_answer.get("answer_id")
        and required_evidence.issubset(set(memory.get("provenance", {}).get("evidence", [])))
        for memory in memories
    )
    if not valid_memory:
        raise IntegrityError("routine has no linked confirmed procedural memory", code="EVIDENCE_INVALID")


def compile_skill(store: EventStore, profile_id: str, routine_id: str) -> dict[str, Any]:
    routine = store.get_routine(profile_id, routine_id)
    if routine.get("status") == "compilable":
        reference = routine.get("compiled_skill", {})
        skill_id = reference.get("skill_id")
        version = reference.get("version")
        if not isinstance(skill_id, str) or not isinstance(version, str):
            raise IntegrityError("compiled routine lost its skill reference", code="EVIDENCE_INVALID")
        skill = store.get_skill(profile_id, skill_id, version)
        verify_compiled_skill(store, profile_id, skill)
        return skill
    if routine.get("status") != "confirmed":
        raise IntegrityError("only a holdout-confirmed routine can be compiled", code="NOT_COMPILABLE")
    if (
        routine.get("intent") != REFERENCE_TEMPLATE["intent"]
        or routine.get("effect") != REFERENCE_TEMPLATE["effect"]
    ):
        raise IntegrityError(
            "routine has no reviewed compilation template in release 0.1.0",
            code="UNSUPPORTED_ROUTINE_TEMPLATE",
        )
    _verify_reference_store_evidence(store, profile_id, routine)
    steps: list[dict[str, Any]] = []
    branch_by_step = {item["step"]: item for item in routine.get("branches", [])}
    for index, action in enumerate(routine.get("prototype_steps", []), start=1):
        step: dict[str, Any] = {
            "id": f"step_{index:02d}_{action}",
            "action": f"synthetic.{action}",
            "deterministic": True,
        }
        branch = branch_by_step.get(action)
        if branch is not None:
            step["when"] = {
                "field": branch["when"]["field"],
                "operator": branch["when"]["operator"],
                "value": branch["when"]["value"],
            }
        steps.append(step)
    permissions = PermissionManifest(
        filesystem_read=("user_selected_synthetic_input",),
        filesystem_write=("user_selected_synthetic_output",),
        applications_activate=("org.apprentice.synthetic-office",),
        external_effects={
            "send_message": False,
            "publish": False,
            "purchase": False,
            "change_access": False,
        },
        max_actions=50,
        max_duration_seconds=180,
        max_model_calls=0,
        max_retries=1,
    ).to_dict()
    skill = {
        "spec_version": SPEC_VERSION,
        "skill_id": REFERENCE_TEMPLATE["skill_id"],
        "version": "0.1.0",
        "intent": REFERENCE_TEMPLATE["description"],
        "inputs": [REFERENCE_TEMPLATE["input"]],
        "preconditions": REFERENCE_TEMPLATE["preconditions"],
        "steps": steps,
        "postconditions": REFERENCE_TEMPLATE["postconditions"],
        "verification": {
            "holdout_cases": list(routine["holdout_evaluation"]),
            "induction_ids": list(routine["induction_ids"]),
            "routine_id": routine_id,
            "all_holdout_passed": all(
                item.get("passed") for item in routine["holdout_evaluation"]
            ),
        },
        "permissions": permissions,
        "risk": {
            "level": "low",
            "requires_preview": True,
            "execution_supported": False,
            "reason": "Release 0.1.0 intentionally compiles and previews but never executes.",
        },
        "lifecycle": {"status": "active"},
        "provenance": {
            "routine_id": routine_id,
            "evidence_refs": list(routine["evidence_refs"]),
            "compiler": "skill-compiler/0.1.0",
            "compiled_at": utc_now(),
        },
    }
    lint_skill(skill)
    store.put_skill(profile_id, skill)
    routine["status"] = "compilable"
    routine["compiled_skill"] = {"skill_id": skill["skill_id"], "version": skill["version"]}
    store.update_routine(profile_id, routine_id, routine)
    return skill


def verify_compiled_skill(store: EventStore, profile_id: str, skill: dict[str, Any]) -> None:
    lint_skill(skill)
    routine_id = skill.get("provenance", {}).get("routine_id")
    if not isinstance(routine_id, str):
        raise IntegrityError("compiled skill has no routine provenance", code="EVIDENCE_INVALID")
    routine = store.get_routine(profile_id, routine_id)
    if routine.get("status") not in {"confirmed", "compilable"}:
        raise IntegrityError("compiled skill routine is not confirmed", code="EVIDENCE_INVALID")
    _verify_reference_store_evidence(store, profile_id, routine)
    verification = skill.get("verification", {})
    if (
        verification.get("routine_id") != routine_id
        or verification.get("induction_ids") != routine.get("induction_ids")
        or verification.get("holdout_cases") != routine.get("holdout_evaluation")
    ):
        raise IntegrityError(
            "compiled skill evidence differs from canonical stored routine",
            code="EVIDENCE_INVALID",
        )


def lint_skill(skill: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    required = {"spec_version", "skill_id", "version", "intent", "preconditions", "steps", "postconditions", "permissions", "risk", "lifecycle"}
    missing = sorted(required - set(skill))
    if missing:
        raise ValidationError(f"Skill IR missing fields: {', '.join(missing)}")
    steps = skill.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValidationError("Skill IR must contain at least one step")
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            raise ValidationError("every skill step requires an id")
        if step["id"] in seen:
            raise ValidationError(f"duplicate step id: {step['id']}")
        seen.add(step["id"])
        action = str(step.get("action", ""))
        if action.startswith("shell.") or action in {"exec", "eval", "subprocess"}:
            raise ValidationError("arbitrary execution action is forbidden")
    permissions = skill["permissions"]
    if permissions.get("network", {}).get("mode") != "deny":
        raise ValidationError("release 0.1.0 requires network deny")
    if permissions.get("filesystem", {}).get("delete"):
        raise ValidationError("release 0.1.0 forbids filesystem deletion")
    if any(permissions.get("external_effects", {}).values()):
        raise ValidationError("release 0.1.0 forbids external effects")
    if skill.get("risk", {}).get("execution_supported") is not False:
        raise ValidationError("release 0.1.0 must remain preview-only")
    if skill.get("lifecycle", {}).get("status") != "active":
        raise IntegrityError("stale Skill IR cannot be previewed or exported", code="STALE_SKILL")
    verification = skill.get("verification", {})
    cases = verification.get("holdout_cases")
    if not isinstance(cases, list) or not cases:
        issues.append({"severity": "error", "code": "HOLDOUT_EMPTY"})
    else:
        seen_cases: set[str] = set()
        seen_demo_ids: set[str] = set()
        induction_ids = verification.get("induction_ids")
        if (
            not isinstance(induction_ids, list)
            or len(induction_ids) != 3
            or len(set(induction_ids)) != 3
            or any(not isinstance(item, str) or not item for item in induction_ids)
        ):
            issues.append({"severity": "error", "code": "INDUCTION_EVIDENCE_INVALID"})
        for case in cases:
            if not isinstance(case, dict):
                issues.append({"severity": "error", "code": "HOLDOUT_CASE_INVALID"})
                continue
            case_id = case.get("episode_id")
            checks = case.get("checks")
            if not isinstance(case_id, str) or not case_id or case_id in seen_cases:
                issues.append({"severity": "error", "code": "HOLDOUT_CASE_ID_INVALID"})
            else:
                seen_cases.add(case_id)
            demo_id = case.get("demo_id")
            if not isinstance(demo_id, str) or demo_id in seen_demo_ids:
                issues.append({"severity": "error", "code": "HOLDOUT_DEMO_ID_INVALID"})
            else:
                seen_demo_ids.add(demo_id)
            if case.get("split") != "holdout":
                issues.append({"severity": "error", "code": "HOLDOUT_SPLIT_INVALID"})
            if isinstance(induction_ids, list) and case_id in induction_ids:
                issues.append({"severity": "error", "code": "HOLDOUT_INDUCTION_OVERLAP"})
            if case.get("passed") is not True:
                issues.append({"severity": "error", "code": "HOLDOUT_CASE_FAILED"})
            if not isinstance(checks, list) or not checks:
                issues.append({"severity": "error", "code": "HOLDOUT_CHECKS_EMPTY"})
                continue
            for check in checks:
                if (
                    not isinstance(check, dict)
                    or not isinstance(check.get("branch_id"), str)
                    or not check.get("branch_id")
                    or type(check.get("expected")) is not bool
                    or type(check.get("observed")) is not bool
                    or check["expected"] != check["observed"]
                ):
                    issues.append({"severity": "error", "code": "HOLDOUT_CHECK_INVALID"})
        recomputed = not issues and len(seen_cases) == len(cases)
        if skill.get("skill_id") == REFERENCE_TEMPLATE["skill_id"] and seen_demo_ids != {"D4", "D5"}:
            issues.append({"severity": "error", "code": "REFERENCE_HOLDOUT_SET_INVALID"})
        if verification.get("all_holdout_passed") is not recomputed:
            issues.append({"severity": "error", "code": "HOLDOUT_AGGREGATE_MISMATCH"})
    if issues:
        codes = ",".join(sorted({item["code"] for item in issues}))
        raise ValidationError(f"Skill IR failed holdout verification: {codes}")
    return []


def preview_skill(skill: dict[str, Any], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    lint_skill(skill)
    supplied = inputs or {}
    if not isinstance(supplied, dict):
        raise ValidationError("preview inputs must be an object")
    safe_inputs, _ = PrivacyGuard().sanitize_payload(supplied)
    return {
        "skill": {"skill_id": skill["skill_id"], "version": skill["version"]},
        "mode": "preview_only",
        "execution_allowed": False,
        "inputs": safe_inputs,
        "planned_steps": [
            {
                "id": step["id"],
                "action": step["action"],
                "condition": step.get("when"),
                "effect": "none_preview_only",
            }
            for step in skill["steps"]
        ],
        "external_effects": [],
        "data_leaves_machine": False,
        "network": "deny",
        "rollback": "not_applicable_no_execution",
        "verification": skill["verification"],
        "reason_code": "PREVIEW_NO_EXECUTION",
    }


def preview_stored_skill(
    store: EventStore,
    profile_id: str,
    skill_id: str,
    version: str,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview only after revalidating current profile-scoped evidence."""

    skill = store.get_skill(profile_id, skill_id, version)
    verify_compiled_skill(store, profile_id, skill)
    return preview_skill(skill, inputs)
