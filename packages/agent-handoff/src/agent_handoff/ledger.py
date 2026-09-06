"""Append-only, hash-chained, idempotent handoff ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ContractError, Handoff, canonical_json, digest


class HandoffLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        if not self.path.is_file():
            raise ContractError("ledger path is not a file")
        rows: list[dict[str, Any]] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                raise ContractError(f"ledger line {number} is empty")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"ledger line {number} is invalid JSON") from exc
            if not isinstance(row, dict):
                raise ContractError(f"ledger line {number} is not an object")
            rows.append(row)
        return rows

    def verify(self) -> dict[str, Any]:
        previous = "0" * 64
        seen: dict[str, str] = {}
        rows = self.entries()
        for index, row in enumerate(rows):
            expected_fields = {"event_id", "handoff", "handoff_sha256", "previous_event_sha256", "event_sha256"}
            if set(row) != expected_fields:
                raise ContractError(f"ledger event {index} has invalid fields")
            handoff = Handoff.from_dict(row["handoff"])
            if row["handoff_sha256"] != handoff.logical_sha256:
                raise ContractError(f"ledger event {index} handoff SHA mismatch")
            if row["previous_event_sha256"] != previous:
                raise ContractError(f"ledger event {index} chain mismatch")
            expected_event = digest({key: value for key, value in row.items() if key != "event_sha256"})
            if row["event_sha256"] != expected_event:
                raise ContractError(f"ledger event {index} SHA mismatch")
            prior = seen.get(row["event_id"])
            if prior is not None:
                raise ContractError(f"ledger duplicate event ID at {index}")
            seen[row["event_id"]] = handoff.logical_sha256
            previous = expected_event
        return {"valid": True, "entries": len(rows), "head_sha256": previous}

    def append(self, handoff: Handoff) -> tuple[dict[str, Any], bool]:
        self.verify()
        rows = self.entries()
        event_id = f"{handoff.mission_id}:{handoff.sequence}"
        for row in rows:
            if row["event_id"] == event_id:
                if row["handoff_sha256"] == handoff.logical_sha256:
                    return row, False
                raise ContractError("idempotency conflict for mission sequence")
        previous = rows[-1]["event_sha256"] if rows else "0" * 64
        row = {
            "event_id": event_id, "handoff": handoff.to_dict(),
            "handoff_sha256": handoff.logical_sha256, "previous_event_sha256": previous,
        }
        row["event_sha256"] = digest(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.verify()
        return row, True

