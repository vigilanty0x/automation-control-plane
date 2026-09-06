"""Generate bounded, evidence-complete and context-safe Markdown handoffs."""

import argparse
import hashlib
import html
import json
import re

MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")
FIELDS = ("title", "summary", "completed", "pending", "evidence", "risks", "next_owner")
LIST_FIELDS = ("completed", "pending", "evidence", "risks")
MAX_ITEMS = 100
MAX_OUTPUT = 100_000


def _text(value, maximum=1_000, *, allow_empty=False):
    if (not isinstance(value, str) or len(value) > maximum or not allow_empty and not value
            or any(ord(char) < 32 for char in value)):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return MARKDOWN.sub(r"\\\1", html.escape(value, quote=False))


def build(data):
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        return {"ok": False, "errors": ["invalid_schema"]}
    title, summary, owner = (_text(data[key], 1_000 if key == "summary" else 200)
                             for key in ("title", "summary", "next_owner"))
    if any(value is None for value in (title, summary, owner)):
        return {"ok": False, "errors": ["invalid_content"]}
    lists = {}
    for key in LIST_FIELDS:
        value = data[key]
        minimum = 1 if key == "evidence" else 0
        if not isinstance(value, list) or not minimum <= len(value) <= MAX_ITEMS:
            return {"ok": False, "errors": ["invalid_lists"]}
        safe = [_text(item) for item in value]
        if any(item is None for item in safe):
            return {"ok": False, "errors": ["invalid_lists"]}
        lists[key] = safe
    lines = [f"# Handoff: {title}", summary, f"Next owner: {owner}"]
    for key, label in (("completed", "Completed"), ("pending", "Pending"),
                       ("evidence", "Evidence"), ("risks", "Risks")):
        lines.extend(["", f"## {label}", *(f"- {item}" for item in lists[key])])
    body = "\n".join(lines)
    if len(body) > MAX_OUTPUT:
        return {"ok": False, "errors": ["output_limit"]}
    return {"ok": True, "markdown": body, "sha256": hashlib.sha256(body.encode()).hexdigest()}


def probe():
    good = build({"title": "d", "summary": "s", "completed": ["c"], "pending": ["p"],
                  "evidence": ["e"], "risks": ["r"], "next_owner": "o"})
    bad = build({"title": "d", "summary": "s"})
    return {"ok": good["ok"] and not bad["ok"], "incomplete_counter_proof": not bad["ok"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "probe"))
    parser.add_argument("--input")
    args = parser.parse_args(argv)
    try:
        data = json.load(open(args.input, encoding="utf-8")) if args.input else None
        out = probe() if args.command == "probe" else build(data)
    except (OSError, UnicodeError, json.JSONDecodeError):
        out = {"ok": False, "errors": ["input_unreadable"]}
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2
