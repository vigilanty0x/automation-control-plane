"""Deterministic bounded quota simulation for declared synthetic tasks."""

import argparse
import hashlib
import json
import re

IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
RESOURCES = ("tokens", "seconds", "cost_micros")
MAX_TASKS = 1_000
MAX_VALUE = 1_000_000_000_000


def _integer(value, low=0, high=MAX_VALUE):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def simulate(data):
    if not isinstance(data, dict) or set(data) != {"budget", "tasks"}:
        return {"ok": False, "decision": "blocked", "errors": ["invalid_input"]}
    budget, tasks = data["budget"], data["tasks"]
    if (not isinstance(budget, dict) or set(budget) != set(RESOURCES)
            or any(not _integer(budget[key]) for key in RESOURCES)
            or not isinstance(tasks, list) or len(tasks) > MAX_TASKS):
        return {"ok": False, "decision": "blocked", "errors": ["invalid_budget_or_tasks"]}
    parsed, identifiers, aggregate = [], set(), 0
    required = {"id", *RESOURCES}
    allowed = required | {"priority"}
    for task in tasks:
        if not isinstance(task, dict) or not required <= set(task) or not set(task) <= allowed:
            return {"ok": False, "decision": "blocked", "errors": ["invalid_task"]}
        identifier = task["id"]
        if (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
                or identifier in identifiers or any(not _integer(task[key]) for key in RESOURCES)
                or not _integer(task.get("priority", 0), 0, 100)):
            return {"ok": False, "decision": "blocked", "errors": ["invalid_task"]}
        identifiers.add(identifier)
        aggregate += sum(task[key] for key in RESOURCES)
        if aggregate > MAX_VALUE:
            return {"ok": False, "decision": "blocked", "errors": ["aggregate_bound"]}
        parsed.append(task)
    remaining, admitted, rejected = dict(budget), [], []
    for task in sorted(parsed, key=lambda item: (-item.get("priority", 0), item["id"])):
        if all(task[key] <= remaining[key] for key in RESOURCES):
            admitted.append(task["id"])
            remaining = {key: remaining[key] - task[key] for key in RESOURCES}
        else:
            rejected.append({"id": task["id"], "reason": "quota"})
    body = {"admitted": admitted, "rejected": rejected, "remaining": remaining}
    return {"ok": True, "decision": "ready", **body,
            "simulation_sha256": hashlib.sha256(json.dumps(body, sort_keys=True,
                                                             separators=(",", ":")).encode()).hexdigest()}


def probe():
    good = simulate({"budget": {"tokens": 10, "seconds": 10, "cost_micros": 10},
                     "tasks": [{"id": "a", "tokens": 1, "seconds": 1, "cost_micros": 1}]})
    bad = simulate({"budget": {"tokens": -1, "seconds": 1, "cost_micros": 1}, "tasks": []})
    return {"ok": good["ok"] and not bad["ok"], "negative_counter_proof": not bad["ok"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("simulate", "probe"))
    parser.add_argument("--input")
    args = parser.parse_args(argv)
    try:
        data = json.load(open(args.input, encoding="utf-8")) if args.input else None
        out = probe() if args.command == "probe" else simulate(data)
    except (OSError, UnicodeError, json.JSONDecodeError):
        out = {"ok": False, "decision": "blocked", "errors": ["input_unreadable"]}
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2
