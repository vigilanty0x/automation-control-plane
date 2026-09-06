from __future__ import annotations

import re
from typing import Any

from ._common import (
    ValidationError,
    blocked,
    evidence,
    expect_bool,
    expect_exact_keys,
    expect_int,
    expect_object,
    expect_str,
)

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_TARGET_STATES = {
    "PROPOSED",
    "REHEARSAL",
    "READY",
    "RELEASED",
    "VERIFIED",
    "ROLLED_BACK",
}


def _git_sha(value: Any, path: str) -> str:
    text = expect_str(value, path, minimum=40, maximum=40)
    if not _GIT_SHA.fullmatch(text):
        raise ValidationError(f"{path}: expected lowercase 40-character Git SHA")
    return text


def rehearse_rollback(data: Any) -> dict[str, Any]:
    try:
        root = expect_object(data)
        expect_exact_keys(root, required=("baseline", "candidate", "checks"))

        baseline = expect_object(root["baseline"], "$.baseline")
        expect_exact_keys(
            baseline,
            required=("target_git_sha", "source_support_state"),
            path="$.baseline",
        )
        baseline_sha = _git_sha(baseline["target_git_sha"], "$.baseline.target_git_sha")
        source_support_state = expect_str(
            baseline["source_support_state"],
            "$.baseline.source_support_state",
            maximum=32,
            identifier=True,
        )
        if source_support_state not in {"active", "deprecated"}:
            raise ValidationError("$.baseline.source_support_state: unsupported state")

        candidate = expect_object(root["candidate"], "$.candidate")
        expect_exact_keys(
            candidate,
            required=(
                "target_git_sha",
                "target_state",
                "redirects_active",
                "aliases_active",
                "consumers_migrated",
                "consumers_total",
            ),
            path="$.candidate",
        )
        candidate_sha = _git_sha(candidate["target_git_sha"], "$.candidate.target_git_sha")
        if candidate_sha == baseline_sha:
            raise ValidationError("$.candidate.target_git_sha: candidate must differ from baseline")
        target_state = expect_str(candidate["target_state"], "$.candidate.target_state", maximum=32)
        if target_state not in _TARGET_STATES:
            raise ValidationError("$.candidate.target_state: unsupported target state")
        redirects_active = expect_bool(candidate["redirects_active"], "$.candidate.redirects_active")
        aliases_active = expect_bool(candidate["aliases_active"], "$.candidate.aliases_active")
        consumers_migrated = expect_int(
            candidate["consumers_migrated"],
            "$.candidate.consumers_migrated",
            minimum=0,
            maximum=100_000,
        )
        consumers_total = expect_int(
            candidate["consumers_total"],
            "$.candidate.consumers_total",
            minimum=0,
            maximum=100_000,
        )
        if consumers_migrated > consumers_total:
            raise ValidationError("$.candidate.consumers_migrated: cannot exceed consumers_total")

        checks = expect_object(root["checks"], "$.checks")
        expect_exact_keys(
            checks,
            required=(
                "baseline_reachable",
                "source_support_restorable",
                "target_disable_rehearsed",
                "redirect_reversal_rehearsed",
                "alias_reversal_rehearsed",
                "consumer_recovery_rehearsed",
            ),
            path="$.checks",
        )
        parsed_checks = {key: expect_bool(value, f"$.checks.{key}") for key, value in checks.items()}
        failed_checks = sorted(key for key, value in parsed_checks.items() if not value)

        steps = [
            "freeze_target_writes",
            "restore_source_support",
            "disable_target_entrypoint",
        ]
        if redirects_active:
            steps.append("reverse_redirects")
        if aliases_active:
            steps.append("reverse_aliases")
        if consumers_migrated:
            steps.append("restore_consumer_configuration")
        steps.extend(("verify_baseline_sha", "verify_durable_core_contract"))

        payload = {
            "baseline": {
                "target_git_sha": baseline_sha,
                "source_support_state": source_support_state,
            },
            "candidate": {
                "target_git_sha": candidate_sha,
                "target_state": target_state,
                "redirects_active": redirects_active,
                "aliases_active": aliases_active,
                "consumers_migrated": consumers_migrated,
                "consumers_total": consumers_total,
            },
            "checks": parsed_checks,
        }
        details = {
            **payload,
            "ordered_steps": steps,
            "failed_checks": failed_checks,
            "mutation_performed": False,
            "rehearsal_only": True,
            "portfolio_gate": "not_run",
        }
        return evidence(
            "rollback_rehearsal",
            "failed" if failed_checks else "passed",
            payload,
            details,
        )
    except ValidationError as exc:
        return blocked("rollback_rehearsal", data, exc)
