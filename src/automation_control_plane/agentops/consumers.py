from __future__ import annotations

from collections import Counter
from typing import Any

from ._common import (
    ValidationError,
    blocked,
    ensure_unique,
    evidence,
    expect_exact_keys,
    expect_int,
    expect_list,
    expect_object,
    expect_str,
)
from .inventory import SOURCE_INVENTORY

_CONSUMER_KINDS = (
    "documentation",
    "fork",
    "import",
    "package",
    "pilot",
    "workflow",
)
_SOURCE_NAMES = tuple(item["repository"] for item in SOURCE_INVENTORY)


def _validate_scope(value: Any) -> tuple[dict[str, Any], bool]:
    scope = expect_object(value, "$.scan_scope")
    expect_exact_keys(
        scope,
        required=(
            "observed_at",
            "expires_at",
            "repositories_expected",
            "repositories_scanned",
            "complete_kinds",
        ),
        path="$.scan_scope",
    )
    observed_at = expect_str(scope["observed_at"], "$.scan_scope.observed_at", maximum=64)
    expires_at = expect_str(scope["expires_at"], "$.scan_scope.expires_at", maximum=64)
    repositories_expected = expect_int(
        scope["repositories_expected"],
        "$.scan_scope.repositories_expected",
        minimum=1,
        maximum=10_000,
    )
    repositories_scanned = expect_int(
        scope["repositories_scanned"],
        "$.scan_scope.repositories_scanned",
        minimum=0,
        maximum=10_000,
    )
    if repositories_scanned > repositories_expected:
        raise ValidationError("$.scan_scope.repositories_scanned: cannot exceed repositories_expected")
    complete_kinds = [
        expect_str(item, f"$.scan_scope.complete_kinds[{index}]", maximum=32, identifier=True)
        for index, item in enumerate(
            expect_list(scope["complete_kinds"], "$.scan_scope.complete_kinds", maximum=len(_CONSUMER_KINDS))
        )
    ]
    ensure_unique(complete_kinds, "$.scan_scope.complete_kinds")
    unknown = sorted(set(complete_kinds) - set(_CONSUMER_KINDS))
    if unknown:
        raise ValidationError(f"$.scan_scope.complete_kinds: unsupported kinds: {', '.join(unknown)}")
    coverage_complete = (
        repositories_scanned == repositories_expected
        and set(complete_kinds) == set(_CONSUMER_KINDS)
    )
    return {
        "observed_at": observed_at,
        "expires_at": expires_at,
        "repositories_expected": repositories_expected,
        "repositories_scanned": repositories_scanned,
        "complete_kinds": complete_kinds,
    }, coverage_complete


def inventory_consumers(data: Any) -> dict[str, Any]:
    try:
        root = expect_object(data)
        expect_exact_keys(root, required=("scan_scope", "sources"))
        scope, coverage_complete = _validate_scope(root["scan_scope"])

        source_entries = expect_list(root["sources"], "$.sources", maximum=len(_SOURCE_NAMES))
        if len(source_entries) != len(_SOURCE_NAMES):
            raise ValidationError(f"$.sources: expected exactly {len(_SOURCE_NAMES)} source entries")

        parsed_sources: list[dict[str, Any]] = []
        seen_sources: list[str] = []
        seen_references: set[tuple[str, str, str, str]] = set()
        kind_counts: Counter[str] = Counter()
        unique_consumers: set[str] = set()
        total_references = 0

        for source_index, item in enumerate(source_entries):
            path = f"$.sources[{source_index}]"
            source = expect_object(item, path)
            expect_exact_keys(source, required=("repository", "references"), path=path)
            repository = expect_str(source["repository"], f"{path}.repository", maximum=64, identifier=True)
            if repository not in _SOURCE_NAMES:
                raise ValidationError(f"{path}.repository: repository is not in the AgentOps source inventory")
            seen_sources.append(repository)

            references = expect_list(source["references"], f"{path}.references", maximum=500)
            parsed_references: list[dict[str, str]] = []
            for ref_index, raw_reference in enumerate(references):
                ref_path = f"{path}.references[{ref_index}]"
                reference = expect_object(raw_reference, ref_path)
                expect_exact_keys(reference, required=("consumer", "kind", "evidence"), path=ref_path)
                consumer = expect_str(reference["consumer"], f"{ref_path}.consumer", maximum=128)
                kind = expect_str(reference["kind"], f"{ref_path}.kind", maximum=32, identifier=True)
                if kind not in _CONSUMER_KINDS:
                    raise ValidationError(f"{ref_path}.kind: unsupported consumer kind")
                evidence_ref = expect_str(reference["evidence"], f"{ref_path}.evidence", maximum=512)
                signature = (repository, consumer, kind, evidence_ref)
                if signature in seen_references:
                    raise ValidationError(f"{ref_path}: duplicate consumer reference")
                seen_references.add(signature)
                kind_counts[kind] += 1
                unique_consumers.add(consumer)
                total_references += 1
                parsed_references.append({"consumer": consumer, "kind": kind, "evidence": evidence_ref})
            parsed_sources.append({"repository": repository, "references": parsed_references})

        ensure_unique(seen_sources, "$.sources.repository")
        missing_sources = sorted(set(_SOURCE_NAMES) - set(seen_sources))
        if missing_sources:
            raise ValidationError(f"$.sources: missing source repositories: {', '.join(missing_sources)}")

        payload = {"scan_scope": scope, "sources": parsed_sources}
        details = {
            **payload,
            "source_count": len(parsed_sources),
            "reference_count": total_references,
            "unique_consumer_count": len(unique_consumers),
            "kind_counts": {kind: kind_counts.get(kind, 0) for kind in _CONSUMER_KINDS},
            "sources_without_references": sorted(
                source["repository"] for source in parsed_sources if not source["references"]
            ),
            "coverage_complete": coverage_complete,
            "mutation_performed": False,
            "portfolio_gate": "not_run",
        }
        return evidence(
            "consumer_inventory",
            "passed" if coverage_complete else "failed",
            payload,
            details,
        )
    except ValidationError as exc:
        return blocked("consumer_inventory", data, exc)
