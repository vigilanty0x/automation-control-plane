from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable

PROJECT = "human-in-the-loop-queue"
REQUIRED_FIELDS = ("request_id", "expires_at", "decision", "audit")
MAX_INPUT_BYTES = 65_536


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _text(value: Any, limit: int = 200) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _clock_value(clock: Callable[[], datetime] | None) -> tuple[datetime, str]:
    value = datetime.now(timezone.utc) if clock is None else clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc), "production" if clock is None else "simulation"


def build_queue_record(record: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    if set(record) != set(REQUIRED_FIELDS):
        raise ValueError("record accepts only request_id, expires_at, decision, and audit; the clock is host-controlled")
    decision = record.get("decision")
    if not _text(record.get("request_id")) or decision not in {"pending", "approved", "rejected", "expired"}:
        raise ValueError("request_id and decision are invalid")
    expires = _instant(record.get("expires_at"), "expires_at")
    audit = record.get("audit")
    if not isinstance(audit, list) or not 1 <= len(audit) <= 500:
        raise ValueError("audit must contain 1-500 entries")
    normalized = []
    effective_expiry = expires
    last_time: datetime | None = None
    for item in audit:
        if not isinstance(item, dict) or set(item) - {"action", "actor", "at", "new_expires_at"} or not _text(item.get("action")) or not _text(item.get("actor")):
            raise ValueError("audit entries require bounded action, actor, and timestamp fields")
        at = _instant(item.get("at"), "audit.at")
        if at > as_of or (last_time is not None and at < last_time):
            raise ValueError("audit timestamps must be ordered and no later than as_of")
        last_time = at
        if item["action"] == "renewed":
            if "new_expires_at" not in item or at > effective_expiry:
                raise ValueError("renewal must be audited no later than the active expiry")
            renewed = _instant(item["new_expires_at"], "new_expires_at")
            if renewed <= effective_expiry:
                raise ValueError("renewal must extend the active expiry")
            effective_expiry = renewed
        elif "new_expires_at" in item:
            raise ValueError("new_expires_at is only valid for renewed actions")
        normalized.append({**item, "at": at.isoformat(), **({"new_expires_at": effective_expiry.isoformat()} if item["action"] == "renewed" else {})})
    expired = effective_expiry <= as_of
    if expired and decision != "expired":
        raise ValueError("a request expires when expires_at is less than or equal to as_of")
    if not expired and decision == "expired":
        raise ValueError("decision cannot be expired before the effective expiry")
    if decision in {"approved", "rejected"}:
        matches = [item for item in normalized if item["action"] == decision]
        if not matches or _instant(matches[-1]["at"], "audit.at") >= effective_expiry:
            raise ValueError("terminal decisions require matching pre-expiry audit evidence")
    return {"request_id": record["request_id"], "as_of": as_of.isoformat(), "effective_expires_at": effective_expiry.isoformat(), "decision": decision, "audit": normalized}


def evaluate(record: Any, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    artifact: Any = None
    safe_record = None
    try:
        if not isinstance(record, dict):
            raise ValueError("record must be a JSON object")
        if len(_canonical(record).encode()) > MAX_INPUT_BYTES:
            raise ValueError("record exceeds 65536 bytes")
        safe_record = record
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            status, reason = "blocked", "missing required fields: " + ", ".join(missing)
        else:
            as_of, mode = _clock_value(clock)
            artifact = build_queue_record(record, as_of=as_of)
            if mode == "simulation":
                status, reason = "simulated", "queue state evaluated with an injected test clock; this is not authorization evidence"
            else:
                status, reason = "passed", "queue state evaluated with the production UTC clock"
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        status, reason = "failed", str(exc)
    mode = "simulation" if clock is not None else "production"
    authorization_evidence = bool(status == "passed" and artifact and artifact["decision"] in {"approved", "rejected"})
    receipt = {"project": PROJECT, "status": status, "reason": reason, "mode": mode, "authorization_evidence": authorization_evidence, "record": safe_record, "queue_record": artifact}
    receipt["evidence_sha256"] = sha256(_canonical(receipt).encode()).hexdigest()
    return receipt
