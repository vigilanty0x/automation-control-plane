from __future__ import annotations

import re
from typing import Any

from ._common import ValidationError, blocked, evidence, expect_exact_keys, expect_list, expect_object, expect_str
from .migration_contracts import MIGRATION_CONTRACTS

_STATIC_COMPLETE_KINDS = {"documentation", "fork", "import", "package", "workflow"}
_RUNTIME_REFERENCE_KINDS = {"fork", "package", "pilot", "workflow"}
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _source_map(inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    sources = expect_list(inventory.get("sources"), "$.consumer_inventory.sources", maximum=64)
    result: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(sources):
        path = f"$.consumer_inventory.sources[{index}]"
        item = expect_object(raw, path)
        expect_exact_keys(item, required=("repository", "references"), path=path)
        repository = expect_str(item["repository"], f"{path}.repository", maximum=128)
        if repository in result:
            raise ValidationError(f"{path}.repository: duplicate repository")
        references = expect_list(item["references"], f"{path}.references", maximum=1_000)
        result[repository] = references
    return result


def _triage_map(triage: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    sources = expect_list(triage.get("sources"), "$.triage.sources", maximum=64)
    result: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(sources):
        path = f"$.triage.sources[{index}]"
        item = expect_object(raw, path)
        if set(item) != {"repository", "candidates"}:
            raise ValidationError(f"{path}: expected repository and candidates only")
        repository = expect_str(item["repository"], f"{path}.repository", maximum=128)
        if repository in result:
            raise ValidationError(f"{path}.repository: duplicate repository")
        result[repository] = expect_list(item["candidates"], f"{path}.candidates", maximum=2_000)
    status = triage.get("status")
    unresolved = triage.get("unresolved")
    triage_complete = status == "passed" and type(unresolved) is int and unresolved == 0
    return result, triage_complete


def _candidate_sha(root: dict[str, Any]) -> str | None:
    raw = root.get("candidate_sha")
    if raw is None:
        return None
    candidate = expect_str(raw, "$.candidate_sha", minimum=40, maximum=40)
    if not _GIT_SHA.fullmatch(candidate):
        raise ValidationError("$.candidate_sha: expected lowercase 40-character Git SHA")
    return candidate


def plan_migration(data: Any) -> dict[str, Any]:
    try:
        root = expect_object(data)
        expect_exact_keys(root, required=("consumer_inventory", "triage"), optional=("candidate_sha",))
        candidate_sha = _candidate_sha(root)
        inventory = expect_object(root["consumer_inventory"], "$.consumer_inventory")
        triage = expect_object(root["triage"], "$.triage")

        scope = expect_object(inventory.get("scan_scope"), "$.consumer_inventory.scan_scope")
        expected = scope.get("repositories_expected")
        scanned = scope.get("repositories_scanned")
        complete_kinds_raw = scope.get("complete_kinds")
        if type(expected) is not int or type(scanned) is not int:
            raise ValidationError("$.consumer_inventory.scan_scope: repository counts must be integers")
        complete_kinds = {
            expect_str(item, "$.consumer_inventory.scan_scope.complete_kinds[]", maximum=32)
            for item in expect_list(complete_kinds_raw, "$.consumer_inventory.scan_scope.complete_kinds", maximum=16)
        }
        static_scope_complete = scanned == expected and _STATIC_COMPLETE_KINDS <= complete_kinds
        pilot_coverage_complete = "pilot" in complete_kinds

        inventory_sources = _source_map(inventory)
        triage_sources, triage_complete = _triage_map(triage)
        contract_names = {item["repository"] for item in MIGRATION_CONTRACTS}
        missing_inventory = sorted(contract_names - set(inventory_sources))
        missing_triage = sorted(contract_names - set(triage_sources))
        if missing_inventory or missing_triage:
            raise ValidationError(
                "source coverage is incomplete: "
                + ", ".join(
                    part
                    for part in (
                        "inventory=" + ",".join(missing_inventory) if missing_inventory else "",
                        "triage=" + ",".join(missing_triage) if missing_triage else "",
                    )
                    if part
                )
            )

        source_plans: list[dict[str, Any]] = []
        adapter_candidates: list[str] = []
        observed_runtime_consumers = 0
        for contract in MIGRATION_CONTRACTS:
            repository = contract["repository"]
            raw_references = inventory_sources[repository]
            runtime_refs: list[dict[str, Any]] = []
            for ref in raw_references:
                if not isinstance(ref, dict):
                    raise ValidationError(f"reference for {repository} must be an object")
                kind = ref.get("kind")
                if kind in _RUNTIME_REFERENCE_KINDS:
                    runtime_refs.append(ref)

            verified_imports = [
                candidate
                for candidate in triage_sources[repository]
                if isinstance(candidate, dict) and candidate.get("classification") == "verified_import"
            ]
            unresolved = [
                candidate
                for candidate in triage_sources[repository]
                if isinstance(candidate, dict) and candidate.get("classification") == "unresolved"
            ]
            runtime_count = len(runtime_refs) + len(verified_imports)
            observed_runtime_consumers += runtime_count

            if unresolved:
                planning_state = "blocked_unresolved_code_reference"
            elif runtime_count:
                planning_state = "consumer_migration_required"
            elif contract["strategy"] == "candidate_adapter":
                planning_state = "adapter_preparable_no_public_runtime_consumer"
                adapter_candidates.append(repository)
            elif contract["strategy"] == "projection_only":
                planning_state = "projection_only_source_retained"
            elif contract["strategy"] == "evidence_only":
                planning_state = "evidence_only_source_retained"
            else:
                planning_state = "incompatible_source_retained"

            source_plans.append(
                {
                    "repository": repository,
                    "source_sha": contract["source_sha"],
                    "strategy": contract["strategy"],
                    "planning_state": planning_state,
                    "observed_runtime_reference_count": runtime_count,
                    "verified_import_count": len(verified_imports),
                    "unresolved_code_reference_count": len(unresolved),
                    "activation_state": "blocked",
                }
            )

        evidence_ready_for_planning = static_scope_complete and triage_complete
        details = {
            "candidate_sha": candidate_sha,
            "source_plans": source_plans,
            "adapter_candidates": adapter_candidates,
            "observed_runtime_reference_count": observed_runtime_consumers,
            "static_scope_complete": static_scope_complete,
            "triage_complete": triage_complete,
            "pilot_coverage_complete": pilot_coverage_complete,
            "planning_evidence_ready": evidence_ready_for_planning,
            "formal_migration_gate": "blocked",
            "legacy_aliases_activated": False,
            "migration_performed": False,
            "irreversible_actions_allowed": False,
            "named_human_approval_required": True,
            "default_branch_live_evidence_required": True,
            "next_actions": [
                action
                for action in (
                    "obtain explicit pilot/adopter completeness attestation" if not pilot_coverage_complete else None,
                    "rerun the formal public consumer evidence workflow from the reviewed default-branch lineage",
                    "obtain named human approval of the final product and migration boundary",
                    "implement only versioned adapters whose candidate_adapter contracts have dedicated fixtures and counter-proofs",
                )
                if action is not None
            ],
            "rule": "planning evidence may prepare reversible work but cannot authorize aliases, consumer mutation, redirect, release, rollback, or archive",
        }
        evidence_input: dict[str, Any] = {"source_plans": source_plans}
        if candidate_sha is not None:
            evidence_input["candidate_sha"] = candidate_sha
        return evidence(
            "migration_plan",
            "passed" if evidence_ready_for_planning else "failed",
            evidence_input,
            details,
        )
    except ValidationError as exc:
        return blocked("migration_plan", data, exc)
