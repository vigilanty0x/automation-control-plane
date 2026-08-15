"""Tamper-evident, bounded offline transcript recording and verification."""

import argparse
import hashlib
import json
import re

HEX64 = re.compile(r"[0-9a-f]{64}")
KINDS = {"input", "output", "tool", "decision", "error"}
MAX_EVENTS = 10_000
MAX_EVENT_BYTES = 65_536
MAX_TRANSCRIPT_BYTES = 10_000_000


def _fail(error, *, integrity=False):
    return {"ok": False, "integrity": integrity, "authenticity": "not_established",
            "errors": [error]}


def _event_data(events):
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return None
    clean, total = [], 0
    for expected, event in enumerate(events, 1):
        if (not isinstance(event, dict) or set(event) != {"sequence", "kind", "content"}
                or event.get("sequence") != expected or isinstance(event.get("sequence"), bool)
                or event.get("kind") not in KINDS):
            return None
        try:
            encoded = json.dumps(event["content"], sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode()
        except (TypeError, ValueError):
            return None
        if len(encoded) > MAX_EVENT_BYTES:
            return None
        total += len(encoded)
        if total > MAX_TRANSCRIPT_BYTES:
            return None
        clean.append(dict(event))
    return clean


def record(events):
    clean = _event_data(events)
    if clean is None:
        return {"ok": False, "errors": ["invalid_events"]}
    chain, previous = [], "0" * 64
    for event in clean:
        body = {**event, "previous_sha256": previous}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        chain.append({**body, "event_sha256": digest})
        previous = digest
    return {"ok": True, "events": chain, "head_sha256": previous, "count": len(chain)}


def verify(transcript, *, expected_head_sha256=None):
    """Verify stored fields, optionally anchoring the chain to a separately trusted head."""
    if (not isinstance(transcript, dict)
            or set(transcript) != {"ok", "events", "head_sha256", "count"}
            or transcript.get("ok") is not True
            or not isinstance(transcript.get("count"), int)
            or isinstance(transcript.get("count"), bool)
            or not isinstance(transcript.get("events"), list)
            or transcript["count"] != len(transcript["events"])
            or not isinstance(transcript.get("head_sha256"), str)
            or not HEX64.fullmatch(transcript["head_sha256"])):
        return _fail("invalid_transcript")
    if expected_head_sha256 is not None and (
            not isinstance(expected_head_sha256, str) or not HEX64.fullmatch(expected_head_sha256)):
        return _fail("invalid_expected_head")
    raw = []
    for event in transcript["events"]:
        if not isinstance(event, dict) or set(event) != {
                "sequence", "kind", "content", "previous_sha256", "event_sha256"}:
            return _fail("invalid_stored_event")
        raw.append({k: event[k] for k in ("sequence", "kind", "content")})
    rebuilt = record(raw)
    if not rebuilt["ok"]:
        return _fail("invalid_event_data")
    for stored, wanted in zip(transcript["events"], rebuilt["events"]):
        if (stored["previous_sha256"] != wanted["previous_sha256"]
                or stored["event_sha256"] != wanted["event_sha256"]):
            return _fail("chain_mismatch")
    if transcript["head_sha256"] != rebuilt["head_sha256"] or transcript["count"] != rebuilt["count"]:
        return _fail("summary_mismatch")
    if expected_head_sha256 is not None and rebuilt["head_sha256"] != expected_head_sha256:
        return _fail("trusted_head_mismatch", integrity=True)
    return {"ok": True, "integrity": True,
            "authenticity": "trusted_head" if expected_head_sha256 is not None else "not_established",
            "errors": [], "head_sha256": rebuilt["head_sha256"], "count": rebuilt["count"]}


def probe():
    good = record([{"sequence": 1, "kind": "input", "content": "demo"}])
    bad = record([{"sequence": 2, "kind": "input", "content": "bad"}])
    return {"ok": good["ok"] and verify(good)["ok"] and not bad["ok"],
            "sequence_counter_proof": not bad["ok"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record", "verify", "probe"))
    parser.add_argument("--input")
    parser.add_argument("--expected-head-sha256")
    args = parser.parse_args(argv)
    try:
        data = json.load(open(args.input, encoding="utf-8")) if args.input else {}
        if args.command == "probe":
            out = probe()
        elif args.command == "record":
            out = record(data.get("events") if isinstance(data, dict) else None)
        else:
            out = verify(data, expected_head_sha256=args.expected_head_sha256)
    except (OSError, UnicodeError, json.JSONDecodeError):
        out = _fail("input_unreadable")
    print(json.dumps(out, sort_keys=True))
    return 0 if out.get("ok", False) else 2
