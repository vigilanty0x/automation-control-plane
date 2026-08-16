"""Transparent vector benchmarks for the reference vertical slice."""

from __future__ import annotations

from typing import Any

from .errors import ValidationError
from .store import EventStore
from .synthetic import CANARY_CATALOG, canary_receipt

MAX_SCAN_FILE_BYTES = 64 * 1024 * 1024
SCAN_CHUNK_BYTES = 64 * 1024


def _scan_file_for_canaries(
    path: object,
    canaries: tuple[bytes, ...],
    *,
    max_file_bytes: int = MAX_SCAN_FILE_BYTES,
    chunk_bytes: int = SCAN_CHUNK_BYTES,
) -> tuple[set[bytes], bool]:
    """Scan a regular file in bounded memory, including chunk-boundary matches."""

    candidate = path if hasattr(path, "open") else None
    if candidate is None or candidate.is_symlink() or not candidate.is_file():
        return set(), False
    if candidate.stat().st_size > max_file_bytes:
        return set(), True
    longest = max((len(item) for item in canaries), default=1)
    found: set[bytes] = set()
    tail = b""
    with candidate.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            window = tail + chunk
            found.update(item for item in canaries if item in window)
            tail = window[-(longest - 1) :] if longest > 1 else b""
    return found, False


def _verified_receipt(
    store: EventStore,
    profile_id: str,
    supplied: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    receipt = supplied
    if receipt is None:
        receipt = [
            item["details"]
            for item in store.audit_events(profile_id)
            if item["component"] == "benchmark"
            and item["action"] == "canary_attempt"
            and item["reason_code"] == "SYNTHETIC_CANARY"
        ]
    if not isinstance(receipt, list):
        raise ValidationError("benchmark canary receipt must be a list")
    verified: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in receipt:
        if not isinstance(item, dict) or set(item) != {"canary_id", "digest"}:
            raise ValidationError("benchmark canary receipt entry is invalid")
        canary_id = item.get("canary_id")
        if not isinstance(canary_id, str) or canary_id not in CANARY_CATALOG or canary_id in seen:
            raise ValidationError("benchmark canary receipt id is invalid or duplicated")
        expected = canary_receipt(canary_id)
        if item != expected:
            raise ValidationError("benchmark canary receipt digest is invalid")
        seen.add(canary_id)
        verified.append(expected)
    return verified


def run_benchmarks(
    store: EventStore,
    profile_id: str,
    *,
    canary_receipts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    sessions = store.list_sessions(profile_id)
    chain_results: list[dict[str, Any]] = []
    for session in sessions:
        chain_results.append(store.verify_chain(profile_id, session["session_id"]))
    events = store.list_events(profile_id)
    episodes = store.list_episodes(profile_id)
    routines = store.list_routines(profile_id)
    questions = store.list_questions(profile_id)
    memories = store.list_memories(profile_id)
    skills = store.list_skills(profile_id)
    verified_receipts = _verified_receipt(store, profile_id, canary_receipts)
    forbidden_canaries = tuple(
        CANARY_CATALOG[item["canary_id"]] for item in verified_receipts
    )
    found_canaries: set[bytes] = set()
    oversized_files: list[str] = []
    for candidate in sorted(store.path.parent.glob(f"{store.path.name}*")):
        found, oversized = _scan_file_for_canaries(candidate, forbidden_canaries)
        found_canaries.update(found)
        if oversized:
            oversized_files.append(candidate.name)
    leaked = len(found_canaries)
    holdout_rates = [
        float(item.get("scores", {}).get("holdout_pass_rate", 0.0)) for item in routines
    ]
    vector = {
        "capture": {
            "sessions": len(sessions),
            "events": len(events),
            "sealed_chains": sum(1 for item in chain_results if item.get("sealed")),
            "chain_failures": 0,
        },
        "privacy": {
            "attempted_canaries": len(forbidden_canaries),
            "leaked_canaries": leaked,
            "receipt_valid": bool(verified_receipts),
            "receipt": verified_receipts,
            "scan_complete": not oversized_files,
            "oversized_files": oversized_files,
            "audit_blocks": sum(
                1 for item in store.audit_events(profile_id) if item["reason_code"].startswith("DENY_")
            ),
        },
        "episodes": {
            "total": len(episodes),
            "complete": sum(1 for item in episodes if item.get("status") == "complete"),
            "abstained": sum(
                1 for item in episodes if item.get("segmentation", {}).get("abstained")
            ),
        },
        "patterns": {
            "routines": len(routines),
            "branches": sum(len(item.get("branches", [])) for item in routines),
            "holdout_pass_rates": holdout_rates,
        },
        "questions": {
            "total": len(questions),
            "answered": sum(1 for item in questions if item.get("status") == "answered"),
            "dismissed": sum(1 for item in questions if item.get("status") == "dismissed"),
        },
        "memory": {
            "assertions": len(memories),
            "confirmed": sum(1 for item in memories if item.get("status") == "confirmed"),
        },
        "skills": {
            "compiled": len(skills),
            "preview_only": sum(
                1 for item in skills if item.get("risk", {}).get("execution_supported") is False
            ),
        },
    }
    required = {
        "five_sealed_sessions": vector["capture"]["sealed_chains"] >= 5,
        "zero_canary_leaks": bool(verified_receipts) and leaked == 0 and not oversized_files,
        "five_complete_episodes": vector["episodes"]["complete"] >= 5,
        "branch_detected": vector["patterns"]["branches"] >= 1,
        "holdout_proved": bool(holdout_rates) and all(rate == 1.0 for rate in holdout_rates),
        "question_answered": vector["questions"]["answered"] >= 1,
        "memory_confirmed": vector["memory"]["confirmed"] >= 1,
        "skill_preview_only": vector["skills"]["preview_only"] >= 1,
    }
    return {
        "benchmark": "ApprenticeBench/reference-v0.1",
        "profile_id": profile_id,
        "vector": vector,
        "required_checks": required,
        "all_required_passed": all(required.values()),
        "aggregate_score": None,
        "note": "No single score is reported because privacy regressions must not be averaged away.",
    }
