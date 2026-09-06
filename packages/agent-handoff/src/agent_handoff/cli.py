"""CLI for validation, rendering, ledger persistence, and probes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .ledger import HandoffLedger
from .models import ContractError, Handoff
from .probes import functional_probe, liveness_probe, readiness_probe
from .render import render_json, render_markdown


def _load(path: str) -> Handoff:
    target = Path(path)
    if not target.is_file():
        raise ContractError(f"handoff file does not exist: {target}")
    if target.stat().st_size > 1_000_000:
        raise ContractError("handoff file exceeds 1 MB")
    return Handoff.from_json(target.read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agent-handoff")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--input", required=True)
    render = commands.add_parser("render")
    render.add_argument("--input", required=True)
    render.add_argument("--format", required=True, choices=["json", "markdown"])
    render.add_argument("--output")
    append = commands.add_parser("append")
    append.add_argument("--input", required=True)
    append.add_argument("--ledger", required=True)
    verify = commands.add_parser("verify-ledger")
    verify.add_argument("--ledger", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--level", required=True, choices=["liveness", "readiness", "functional"])
    demo = commands.add_parser("demo")
    demo.add_argument("--format", default="markdown", choices=["json", "markdown"])
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            handoff = _load(args.input)
            print(json.dumps({"valid": True, "handoff_id": handoff.handoff_id, "logical_sha256": handoff.logical_sha256}, sort_keys=True))
            return 0
        if args.command == "render":
            handoff = _load(args.input)
            output = render_json(handoff) if args.format == "json" else render_markdown(handoff)
            if args.output:
                target = Path(args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(output, encoding="utf-8")
            else:
                print(output, end="")
            return 0
        if args.command == "append":
            row, appended = HandoffLedger(args.ledger).append(_load(args.input))
            print(json.dumps({"appended": appended, "event_id": row["event_id"], "event_sha256": row["event_sha256"]}, sort_keys=True))
            return 0
        if args.command == "verify-ledger":
            print(json.dumps(HandoffLedger(args.ledger).verify(), sort_keys=True))
            return 0
        if args.command == "probe":
            probes = {"liveness": liveness_probe, "readiness": readiness_probe, "functional": functional_probe}
            result = probes[args.level]()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "demo":
            path = Path(__file__).resolve().parents[2] / "examples" / "handoff.json"
            handoff = _load(str(path if path.is_file() else Path("examples/handoff.json")))
            print(render_json(handoff) if args.format == "json" else render_markdown(handoff), end="")
            return 0
    except (ContractError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2

