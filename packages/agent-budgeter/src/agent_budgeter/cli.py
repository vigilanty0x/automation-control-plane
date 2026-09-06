"""Offline CLI for bounded agent budget evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .fixtures import load, replay
from .journal import EvidenceJournal
from .models import ContractError
from .probes import functional, liveness, readiness


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(prog="agent-budgeter"); commands=root.add_subparsers(dest="command",required=True)
    fixture=commands.add_parser("fixture"); fixture.add_argument("path",type=Path); fixture.add_argument("--journal",type=Path); fixture.add_argument("--output",type=Path)
    probe=commands.add_parser("probe"); probe.add_argument("mode",choices=("liveness","readiness","functional"))
    demo=commands.add_parser("demo"); demo.add_argument("--journal",type=Path)
    return root


def _demo() -> dict:
    return {"schema_version":"1.0","global_limit":{"calls":10,"time_ms":10000,"tokens":10000},
            "agents":[{"agent_id":"writer","owner":"synthetic-owner","capabilities":["code"],"permissions":["local"],"limit":{"calls":5,"time_ms":5000,"tokens":5000},"max_retries":1}],
            "missions":[{"mission_id":"demo","agent_id":"writer","required_capability":"code","required_permission":"local","limit":{"calls":3,"time_ms":3000,"tokens":3000}}],
            "operations":[{"action":"reserve","operation_id":"demo-reserve","mission_id":"demo","amount":{"calls":1,"time_ms":1000,"tokens":1000}}]}


def run(argv: Sequence[str] | None=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.command == "probe": output={"liveness":liveness,"readiness":readiness,"functional":functional}[args.mode](); code=0 if output["healthy"] else 3
        else:
            journal=EvidenceJournal(args.journal) if args.journal else None
            output=replay(load(args.path) if args.command=="fixture" else _demo(),journal)
            code=0 if all(item["decision"]=="accepted" for item in output["results"]) else 2
            if getattr(args,"output",None): args.output.write_text(json.dumps(output,sort_keys=True)+"\n",encoding="utf-8")
        print(json.dumps(output,indent=2,sort_keys=True)); return code
    except (ContractError,OSError) as exc:
        print(json.dumps({"success":False,"decision":"blocked","error":type(exc).__name__,"message":str(exc)},sort_keys=True)); return 4


def main() -> None: raise SystemExit(run())
if __name__ == "__main__": main()

