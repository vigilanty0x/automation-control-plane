from __future__ import annotations
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any

PROJECT = "human-in-the-loop-queue"
REQUIRED_FIELDS = ["request_id","expires_at","decision","audit"]

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

def _string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)

def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def build_queue_record(record: dict[str, Any]) -> dict[str, Any]:
    if not _text(record["request_id"]) or record["decision"] not in {"pending", "approved", "rejected", "expired"}:
        raise ValueError("request id and decision are invalid")
    try:
        expires = datetime.fromisoformat(str(record["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be ISO-8601") from exc
    audit = record["audit"]
    if not isinstance(audit, list) or not audit or any(not isinstance(item, dict) or not _text(item.get("action")) or not _text(item.get("actor")) for item in audit):
        raise ValueError("audit entries require action and actor")
    if record["decision"] in {"approved", "rejected"} and not any(item["action"] == record["decision"] for item in audit):
        raise ValueError("terminal decisions require matching audit evidence")
    return {"request_id": record["request_id"], "expires_at": expires.isoformat(), "decision": record["decision"], "audit_count": len(audit)}

def evaluate(record: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    artifact: Any = None
    if missing:
        status = "blocked"
        reason = "missing required fields: " + ", ".join(missing)
    else:
        try:
            artifact = build_queue_record(record)
            status = "passed"
            reason = "build_queue_record completed"
        except (TypeError, ValueError, KeyError) as exc:
            status = "failed"
            reason = str(exc)
    receipt = {"project": PROJECT, "status": status, "reason": reason, "record": record, "queue_record": artifact}
    receipt["evidence_sha256"] = sha256(_canonical(receipt).encode()).hexdigest()
    return receipt

