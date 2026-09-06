"""Append-only idempotent JSONL evidence."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

from .models import ContractError, OperationResult, canonical_json, sha256_json


class EvidenceJournal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_handle(self, handle) -> list[dict[str, Any]]:
        events = []
        seen = set()
        for number, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ContractError(f"journal line {number} is truncated")
            try: event = json.loads(line)
            except json.JSONDecodeError as exc: raise ContractError(f"journal line {number} is invalid") from exc
            if set(event) != {"event_id", "operation_id", "payload"}:
                raise ContractError("journal event fields are invalid")
            expected = sha256_json({"operation_id": event["operation_id"], "payload": event["payload"]})
            if event["event_id"] != expected or event["event_id"] in seen:
                raise ContractError("journal event identity is invalid or duplicated")
            seen.add(event["event_id"]); events.append(event)
        return events

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists(): return []
        with self.path.open("r", encoding="utf-8", newline="") as handle: return self._read_handle(handle)

    def append(self, result: OperationResult) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = result.to_dict()
        event = {"operation_id": result.operation_id, "payload": payload}
        event["event_id"] = sha256_json(event)
        with self.path.open("a+", encoding="utf-8", newline="") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX); handle.seek(0)
            events = self._read_handle(handle)
            existing = [item for item in events if item["operation_id"] == result.operation_id]
            if existing:
                if existing[0]["payload"] != payload: raise ContractError("idempotency conflict in journal")
                return False
            handle.seek(0, os.SEEK_END); handle.write(canonical_json(event) + "\n"); handle.flush(); os.fsync(handle.fileno())
            return True

