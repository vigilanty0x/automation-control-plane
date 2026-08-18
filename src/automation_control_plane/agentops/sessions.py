from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ._common import (
    ValidationError,
    blocked,
    canonical_json,
    ensure_json,
    ensure_unique,
    evidence,
    expect_exact_keys,
    expect_int,
    expect_list,
    expect_object,
    expect_sha256,
    expect_str,
    json_sha256,
)

MAX_EVENTS = 10_000
MAX_EVENT_BYTES = 65_536
MAX_TOTAL_EVENT_BYTES = 10_000_000
_ZERO_SHA256 = "0" * 64
_SENSITIVE_KEY = re.compile(
    r"^(?:api[_-]?key|authorization|cookie|credential|password|passphrase|private[_-]?key|secret|set[_-]?cookie|token)$",
    re.IGNORECASE,
)
_SENSITIVE_PARTS = {"authorization", "cookie", "credential", "password", "passphrase", "secret", "token"}


def _validate_timestamp(value: Any, path: str) -> tuple[str, datetime]:
    text = expect_str(value, path, maximum=64)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{path}: timezone-aware timestamp is required")
    return text, parsed


def _reject_sensitive_keys(value: Any, path: str = "$.data") -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized_key = key.casefold().replace("-", "_")
            parts = set(normalized_key.split("_"))
            if _SENSITIVE_KEY.fullmatch(key) or parts.intersection(_SENSITIVE_PARTS):
                raise ValidationError(f"{path}.{key}: sensitive key is not accepted")
            _reject_sensitive_keys(item, f"{path}.{key}")
    elif type(value) is list:
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, f"{path}[{index}]")


def _validate_event(raw_event: Any, path: str) -> tuple[dict[str, Any], datetime, int]:
    event = expect_object(raw_event, path)
    expect_exact_keys(
        event,
        required=("event_id", "actor", "type", "at", "data"),
        path=path,
    )
    event_id = expect_str(event["event_id"], f"{path}.event_id", identifier=True)
    actor = expect_str(event["actor"], f"{path}.actor", identifier=True)
    event_type = expect_str(event["type"], f"{path}.type", identifier=True)
    at, parsed_at = _validate_timestamp(event["at"], f"{path}.at")
    ensure_json(event["data"], f"{path}.data")
    _reject_sensitive_keys(event["data"], f"{path}.data")
    normalized = {
        "event_id": event_id,
        "actor": actor,
        "type": event_type,
        "at": at,
        "data": event["data"],
    }
    byte_count = len(canonical_json(normalized).encode("utf-8"))
    if byte_count > MAX_EVENT_BYTES:
        raise ValidationError(
            f"{path}: serialized event exceeds {MAX_EVENT_BYTES} bytes"
        )
    return normalized, parsed_at, byte_count


def record_session(payload: Any) -> dict[str, Any]:
    try:
        return _record_session(payload)
    except ValidationError as exc:
        return blocked("session_record", payload, exc)


def _record_session(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(
        root,
        required=("session_id", "events"),
        optional=("previous_head_sha256",),
    )
    session_id = expect_str(root["session_id"], "$.session_id", identifier=True)
    previous = (
        expect_sha256(root["previous_head_sha256"], "$.previous_head_sha256")
        if "previous_head_sha256" in root
        else _ZERO_SHA256
    )
    initial_previous = previous
    raw_events = expect_list(root["events"], "$.events", maximum=MAX_EVENTS)
    events: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    total_bytes = 0
    for index, raw_event in enumerate(raw_events):
        event, parsed_at, byte_count = _validate_event(raw_event, f"$.events[{index}]")
        events.append(event)
        timestamps.append(parsed_at)
        total_bytes += byte_count
        if total_bytes > MAX_TOTAL_EVENT_BYTES:
            raise ValidationError(
                f"$.events: aggregate content exceeds {MAX_TOTAL_EVENT_BYTES} bytes"
            )
    ensure_unique((event["event_id"] for event in events), "$.events")
    if any(current <= previous_at for previous_at, current in zip(timestamps, timestamps[1:])):
        raise ValidationError("$.events: timestamps must be strictly increasing")

    chain: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        digest_input = {
            "session_id": session_id,
            "index": index,
            "previous_sha256": previous,
            "event": event,
        }
        event_sha256 = json_sha256(digest_input)
        chain.append({**digest_input, "event_sha256": event_sha256})
        previous = event_sha256
    details = {
        "session_id": session_id,
        "event_count": len(events),
        "initial_previous_sha256": initial_previous,
        "head_sha256": previous,
        "chain": chain,
        "authenticity_status": "not_proven",
        "persistence_status": "not_performed",
    }
    return evidence("session_record", "passed", payload, details)


def verify_session(payload: Any) -> dict[str, Any]:
    try:
        return _verify_session(payload)
    except ValidationError as exc:
        return blocked("session_verify", payload, exc)


def _verify_session(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(
        root,
        required=("session_id", "chain"),
        optional=("expected_initial_sha256", "expected_head_sha256"),
    )
    session_id = expect_str(root["session_id"], "$.session_id", identifier=True)
    raw_chain = expect_list(root["chain"], "$.chain", maximum=MAX_EVENTS)
    expected_initial = (
        expect_sha256(root["expected_initial_sha256"], "$.expected_initial_sha256")
        if "expected_initial_sha256" in root
        else None
    )
    expected_head = (
        expect_sha256(root["expected_head_sha256"], "$.expected_head_sha256")
        if "expected_head_sha256" in root
        else None
    )

    previous = expected_initial
    mismatch: dict[str, Any] | None = None
    event_ids: list[str] = []
    previous_timestamp: datetime | None = None
    total_bytes = 0
    observed_initial = _ZERO_SHA256
    observed_head = _ZERO_SHA256
    for index, raw_entry in enumerate(raw_chain):
        path = f"$.chain[{index}]"
        entry = expect_object(raw_entry, path)
        expect_exact_keys(
            entry,
            required=(
                "session_id",
                "index",
                "previous_sha256",
                "event",
                "event_sha256",
            ),
            path=path,
        )
        entry_session = expect_str(
            entry["session_id"], f"{path}.session_id", identifier=True
        )
        entry_index = expect_int(entry["index"], f"{path}.index", minimum=0, maximum=MAX_EVENTS)
        entry_previous = expect_sha256(
            entry["previous_sha256"], f"{path}.previous_sha256"
        )
        event_sha256 = expect_sha256(entry["event_sha256"], f"{path}.event_sha256")
        event, event_timestamp, byte_count = _validate_event(entry["event"], f"{path}.event")
        total_bytes += byte_count
        if total_bytes > MAX_TOTAL_EVENT_BYTES:
            raise ValidationError(
                f"$.chain: aggregate content exceeds {MAX_TOTAL_EVENT_BYTES} bytes"
            )
        event_ids.append(event["event_id"])
        if index == 0:
            observed_initial = entry_previous
            if previous is None:
                previous = observed_initial
        digest_input = {
            "session_id": entry_session,
            "index": entry_index,
            "previous_sha256": entry_previous,
            "event": event,
        }
        recomputed = json_sha256(digest_input)
        checks = (
            (entry_session == session_id, "session_id_mismatch"),
            (entry_index == index, "index_mismatch"),
            (entry_previous == previous, "previous_hash_mismatch"),
            (event_sha256 == recomputed, "event_hash_mismatch"),
        )
        failed = next((reason for ok, reason in checks if not ok), None)
        if failed is None and previous_timestamp is not None and event_timestamp <= previous_timestamp:
            failed = "timestamp_order_mismatch"
        if failed is not None and mismatch is None:
            mismatch = {"index": index, "reason": failed}
        previous = event_sha256
        previous_timestamp = event_timestamp
        observed_head = event_sha256
    ensure_unique(event_ids, "$.chain")
    if not raw_chain:
        observed_initial = expected_initial or _ZERO_SHA256
        observed_head = observed_initial
    if expected_head is not None and observed_head != expected_head and mismatch is None:
        mismatch = {"index": len(raw_chain), "reason": "expected_head_mismatch"}

    status = "failed" if mismatch else "passed"
    authenticity = (
        "verified"
        if status == "passed" and expected_initial is not None and expected_head is not None
        else "not_proven"
    )
    details = {
        "session_id": session_id,
        "event_count": len(raw_chain),
        "initial_sha256": observed_initial,
        "head_sha256": observed_head,
        "mismatch": mismatch,
        "integrity_status": "failed" if mismatch else "verified",
        "authenticity_status": authenticity,
    }
    return evidence("session_verify", status, payload, details)
