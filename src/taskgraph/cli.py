from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .engine import TaskGraphEngine
from .models import ContractError, Evidence, GraphSpec
from .probes import functional_probe, liveness_probe, readiness_probe
from .store import TaskStore


def _graph(path: str) -> GraphSpec:
    target = Path(path)
    if not target.is_file():
        raise ContractError(f"graph does not exist: {target}")
    if target.stat().st_size > 2_000_000:
        raise ContractError("graph exceeds 2 MB")
    return GraphSpec.from_json(target.read_text(encoding="utf-8"))


def _evidence(path: str) -> list[Evidence]:
    target = Path(path)
    if not target.is_file():
        raise ContractError(f"evidence does not exist: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid evidence JSON: {exc.msg}") from exc
    if not isinstance(raw, list) or len(raw) > 100:
        raise ContractError("evidence must be an array with at most 100 items")
    return [Evidence.from_dict(item) for item in raw]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="taskgraph")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate"); validate.add_argument("--graph", required=True)
    init = commands.add_parser("init"); init.add_argument("--graph", required=True); init.add_argument("--db", required=True)
    claim = commands.add_parser("claim"); claim.add_argument("--db", required=True); claim.add_argument("--graph-id", required=True); claim.add_argument("--worker", required=True); claim.add_argument("--now", type=int, required=True); claim.add_argument("--lease-seconds", type=int, default=300)
    complete = commands.add_parser("complete"); complete.add_argument("--db", required=True); complete.add_argument("--graph-id", required=True); complete.add_argument("--task-id", required=True); complete.add_argument("--worker", required=True); complete.add_argument("--evidence", required=True); complete.add_argument("--event-id", required=True)
    fail = commands.add_parser("fail"); fail.add_argument("--db", required=True); fail.add_argument("--graph-id", required=True); fail.add_argument("--task-id", required=True); fail.add_argument("--worker", required=True); fail.add_argument("--error", required=True); fail.add_argument("--event-id", required=True)
    status = commands.add_parser("status"); status.add_argument("--db", required=True); status.add_argument("--graph-id", required=True)
    resume = commands.add_parser("resume"); resume.add_argument("--db", required=True); resume.add_argument("--graph-id", required=True); resume.add_argument("--now", type=int, required=True)
    probe = commands.add_parser("probe"); probe.add_argument("--level", required=True, choices=["liveness", "readiness", "functional"])
    demo = commands.add_parser("demo"); demo.add_argument("--workspace", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "validate":
            graph = _graph(args.graph)
            print(json.dumps({"valid": True, "graph_id": graph.graph_id, "graph_sha256": graph.digest, "topological_order": graph.topological_order()}))
            return 0
        if args.command == "probe":
            probes = {"liveness": liveness_probe, "readiness": readiness_probe, "functional": functional_probe}
            result = probes[args.level](); print(json.dumps(result, sort_keys=True)); return 0 if result["ok"] else 1
        if args.command == "demo":
            workspace = Path(args.workspace); workspace.mkdir(parents=True, exist_ok=True)
            graph_path = Path(__file__).resolve().parents[2] / "examples" / "graph.json"
            if not graph_path.is_file(): graph_path = Path("examples/graph.json")
            graph = _graph(str(graph_path)); store = TaskStore(workspace / "taskgraph.db"); engine = TaskGraphEngine(store); created = engine.register(graph)
            print(json.dumps({"created": created, "snapshot": engine.snapshot(graph.graph_id)}, sort_keys=True)); return 0
        store = TaskStore(args.db)
        engine = TaskGraphEngine(store)
        if args.command == "init":
            graph = _graph(args.graph); print(json.dumps({"created": engine.register(graph), "graph_id": graph.graph_id, "graph_sha256": graph.digest}, sort_keys=True)); return 0
        if args.command == "claim":
            print(json.dumps({"claimed": engine.claim(args.graph_id, args.worker, args.now, args.lease_seconds)}, sort_keys=True)); return 0
        if args.command == "complete":
            added = engine.complete(args.graph_id, args.task_id, args.worker, _evidence(args.evidence), {}, args.event_id); print(json.dumps({"recorded": added}, sort_keys=True)); return 0
        if args.command == "fail":
            state, added = engine.fail(args.graph_id, args.task_id, args.worker, args.error, args.event_id); print(json.dumps({"state": state, "recorded": added}, sort_keys=True)); return 0
        if args.command == "status":
            snapshot = engine.snapshot(args.graph_id); print(json.dumps(snapshot, indent=2, sort_keys=True)); return 0 if snapshot["success"] else 1
        if args.command == "resume":
            print(json.dumps({"resumed": engine.resume_expired(args.graph_id, args.now)}, sort_keys=True)); return 0
    except (ContractError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    finally:
        if store is not None: store.close()
    return 2

