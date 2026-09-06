"""Bounded JSONL ingestion through the same privacy and integrity boundary as adapters."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from .errors import ApprenticeError, ValidationError
from .privacy import PrivacyGuard
from .store import EventStore
from .strictjson import loads_bytes

MAX_JSONL_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_EVENTS = 10_000
_open_fstat = os.fstat


def ingest_jsonl(
    store: EventStore,
    profile_id: str,
    path: str | Path,
    *,
    metadata: dict[str, Any] | None = None,
    guard: PrivacyGuard | None = None,
) -> dict[str, Any]:
    """Import one strict Event object per line and seal an auditable session.

    The source file is never copied into the database. Events cross the closed Event
    contract and privacy guard individually before they can be appended.
    """

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("JSONL path must be a regular non-symlink file")
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        opened = _open_fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            os.close(descriptor)
            raise ValidationError("JSONL path must resolve to a regular file")
    except OSError as exc:
        raise ValidationError(f"cannot safely open JSONL file: {exc}") from exc
    if opened.st_size > MAX_JSONL_BYTES:
        os.close(descriptor)
        raise ValidationError(f"JSONL file exceeds {MAX_JSONL_BYTES} bytes")
    privacy = guard or PrivacyGuard()
    try:
        session_id = store.create_session(
            profile_id,
            mode="import",
            source="jsonl-import/0.1.0",
            metadata=metadata or {},
        )
    except BaseException:
        os.close(descriptor)
        raise
    accepted = 0
    blocked = 0
    lines = 0
    total_bytes = 0
    try:
        with os.fdopen(descriptor, "rb") as handle:
            while True:
                raw_line = handle.readline(MAX_LINE_BYTES + 1)
                if not raw_line:
                    break
                line_number = lines + 1
                lines = line_number
                total_bytes += len(raw_line)
                if total_bytes > MAX_JSONL_BYTES:
                    raise ValidationError(f"JSONL stream exceeds {MAX_JSONL_BYTES} bytes")
                if len(raw_line) > MAX_LINE_BYTES:
                    raise ValidationError(f"JSONL line {line_number} exceeds {MAX_LINE_BYTES} bytes")
                payload = raw_line.rstrip(b"\r\n")
                if not payload:
                    raise ValidationError(f"JSONL line {line_number} is empty")
                if line_number > MAX_EVENTS:
                    raise ValidationError(f"JSONL event count exceeds {MAX_EVENTS}")
                event = loads_bytes(payload, max_bytes=MAX_LINE_BYTES, max_depth=20, max_nodes=20_000)
                if not isinstance(event, dict):
                    raise ValidationError(f"JSONL line {line_number} must contain an Event object")
                normalized = dict(event)
                normalized["source"] = "jsonl-import"
                saved = store.append_event(profile_id, session_id, normalized, privacy)
                if saved is None:
                    blocked += 1
                else:
                    accepted += 1
        if lines == 0:
            raise ValidationError("JSONL file contains no events")
        if accepted == 0:
            raise ValidationError("JSONL import contains no persistable events")
        store.end_session(profile_id, session_id, status="completed")
    except Exception as exc:
        try:
            store.end_session(profile_id, session_id, status="incomplete")
        except ApprenticeError:
            pass
        if isinstance(exc, OSError):
            raise ValidationError(f"JSONL read failed: {exc}") from exc
        raise
    return {
        "adapter": "jsonl-import/0.1.0",
        "profile_id": profile_id,
        "session_id": session_id,
        "status": "completed",
        "lines": lines,
        "bytes_read": total_bytes,
        "events_accepted": accepted,
        "events_blocked": blocked,
        "source_copied": False,
    }
