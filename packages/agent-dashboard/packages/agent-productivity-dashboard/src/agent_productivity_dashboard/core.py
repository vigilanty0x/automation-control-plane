from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any

PROJECT = "agent-productivity-dashboard"
REQUIRED_FIELDS = ("agent", "completed", "failed", "retries", "elapsed_ms")
MAX_INPUT_BYTES = 65_536
MAX_COUNT = 1_000_000_000
MAX_ELAPSED_MS = 31_536_000_000


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _text(value: Any, limit: int = 200) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _count(value: Any, *, maximum: int = MAX_COUNT) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    if not _text(record.get("agent")):
        raise ValueError("agent must be a bounded single-line string")
    for key in ("completed", "failed", "retries"):
        if not _count(record.get(key)):
            raise ValueError("counts must be non-boolean integers between 0 and 1000000000")
    if not _count(record.get("elapsed_ms"), maximum=MAX_ELAPSED_MS) or record["elapsed_ms"] == 0:
        raise ValueError("elapsed_ms must be between 1 and 31536000000")
    total = record["completed"] + record["failed"]
    if total <= 0 or total > MAX_COUNT:
        raise ValueError("a bounded measured workload is required")
    return {
        "agent": record["agent"],
        "total": total,
        "reliability": round(record["completed"] / total, 6),
        "throughput_per_hour": round(record["completed"] * 3_600_000 / record["elapsed_ms"], 6),
        "retries": record["retries"],
    }


def calculate_metrics(record: dict[str, Any]) -> dict[str, Any]:
    current = _metrics(record)
    trend = record.get("trend", [])
    if not isinstance(trend, list) or len(trend) > 365:
        raise ValueError("trend must contain at most 365 metric points")
    normalized_trend = []
    previous: datetime | None = None
    for point in trend:
        if not isinstance(point, dict) or "as_of" not in point:
            raise ValueError("each trend point requires as_of and metric fields")
        try:
            as_of = datetime.fromisoformat(str(point["as_of"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("trend as_of must be timezone-aware ISO-8601") from exc
        if as_of.tzinfo is None or as_of.utcoffset() is None or (previous is not None and as_of <= previous):
            raise ValueError("trend timestamps must be timezone-aware and strictly increasing")
        previous = as_of
        values = _metrics({"agent": record["agent"], **{key: point.get(key) for key in ("completed", "failed", "retries", "elapsed_ms")}})
        normalized_trend.append({"as_of": as_of.isoformat(), **values})
    return {"source": "supplied-metrics", "observed_by_tool": False, "current": current, "trend": normalized_trend}


def evaluate(record: Any) -> dict[str, Any]:
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
            artifact = calculate_metrics(record)
            status, reason = "passed", "dashboard calculated from supplied metrics; no observation was performed"
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        status, reason = "failed", str(exc)
    receipt = {"project": PROJECT, "status": status, "reason": reason, "record": safe_record, "metrics": artifact}
    receipt["evidence_sha256"] = sha256(_canonical(receipt).encode()).hexdigest()
    return receipt
