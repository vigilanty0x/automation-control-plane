"""Fail-closed planning for bounded context-window sections."""

import argparse
import hashlib
import json
import re

NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_TOKENS = 10_000_000
MAX_SECTIONS = 500


def _integer(value, low=0, high=MAX_TOKENS):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def budget(data):
    if not isinstance(data, dict) or set(data) != {"window_tokens", "output_reserve", "sections"}:
        return {"ok": False, "decision": "blocked", "errors": ["invalid_input"]}
    window, reserve, sections = data["window_tokens"], data["output_reserve"], data["sections"]
    if (not _integer(window, 1) or not _integer(reserve) or reserve >= window
            or not isinstance(sections, list) or len(sections) > MAX_SECTIONS):
        return {"ok": False, "decision": "blocked", "errors": ["invalid_input"]}
    parsed, names, total = [], set(), 0
    for section in sections:
        if (not isinstance(section, dict) or not {"name", "tokens", "required"} <= set(section)
                or not set(section) <= {"name", "tokens", "required", "priority"}
                or not isinstance(section["name"], str) or not NAME.fullmatch(section["name"])
                or section["name"] in names or not _integer(section["tokens"])
                or not isinstance(section["required"], bool)
                or not _integer(section.get("priority", 0), 0, 100)):
            return {"ok": False, "decision": "blocked", "errors": ["invalid_section"]}
        names.add(section["name"])
        total += section["tokens"]
        if total > MAX_TOKENS:
            return {"ok": False, "decision": "blocked", "errors": ["aggregate_bound"]}
        parsed.append(section)
    available = window - reserve
    required = sorted((section for section in parsed if section["required"]), key=lambda item: item["name"])
    optional = sorted((section for section in parsed if not section["required"]),
                      key=lambda item: (-item.get("priority", 0), item["name"]))
    used = sum(section["tokens"] for section in required)
    if used > available:
        return {"ok": False, "decision": "blocked", "errors": ["required_overflow"],
                "required_tokens": used, "available": available}
    selected, dropped = [section["name"] for section in required], []
    for section in optional:
        if used + section["tokens"] <= available:
            selected.append(section["name"])
            used += section["tokens"]
        else:
            dropped.append(section["name"])
    body = {"selected": selected, "dropped": dropped, "used": used,
            "available": available, "output_reserve": reserve}
    return {"ok": True, "decision": "ready" if not dropped else "degraded", **body,
            "plan_sha256": hashlib.sha256(json.dumps(body, sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()}


def probe():
    good = budget({"window_tokens": 10, "output_reserve": 2,
                   "sections": [{"name": "system", "tokens": 2, "required": True}]})
    bad = budget({"window_tokens": 4, "output_reserve": 2,
                  "sections": [{"name": "system", "tokens": 3, "required": True}]})
    return {"ok": good["ok"] and not bad["ok"], "overflow_counter_proof": not bad["ok"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("budget", "probe"))
    parser.add_argument("--input")
    args = parser.parse_args(argv)
    try:
        data = json.load(open(args.input, encoding="utf-8")) if args.input else None
        out = probe() if args.command == "probe" else budget(data)
    except (OSError, UnicodeError, json.JSONDecodeError):
        out = {"ok": False, "decision": "blocked", "errors": ["input_unreadable"]}
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2
