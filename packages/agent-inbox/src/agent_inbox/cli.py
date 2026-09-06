"""Bounded JSON CLI for the persistent Agent Inbox."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .contract import AgentProfile, CompletionEvidence, ContractError, MissionSpec, MissionStatus, canonical_json
from .inbox import AgentInbox, InboxError, NoMissionAvailable
from .probes import functional_counter_proof, liveness, readiness

MAX_INPUT_BYTES = 1_000_000


def _load(path: Path) -> Mapping[str, Any]:
    if path.stat().st_size > MAX_INPUT_BYTES: raise ContractError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping): raise ContractError("input root must be an object")
    return value


def _inbox(args: argparse.Namespace) -> AgentInbox: return AgentInbox(args.db)
def _print(value: object) -> None: print(canonical_json(value))


def _init(args): _inbox(args).initialize(); _print({"initialized": True, "database": str(args.db), "schema_version": "1.0"}); return 0
def _register(args): _print(_inbox(args).register_agent(AgentProfile.from_dict(_load(args.input)))); return 0
def _enqueue(args): _print(_inbox(args).enqueue(MissionSpec.from_dict(_load(args.input)))); return 0
def _claim(args): _print(_inbox(args).claim(args.agent, lease_seconds=args.lease_seconds)); return 0
def _heartbeat(args): _print(_inbox(args).heartbeat(args.mission, args.lease_token, lease_seconds=args.lease_seconds)); return 0
def _complete(args): _print(_inbox(args).complete(args.mission, args.lease_token, CompletionEvidence.from_dict(_load(args.evidence)))); return 0
def _wait(args): _print(_inbox(args).wait(args.mission, args.lease_token, args.reason)); return 0
def _reject(args): _print(_inbox(args).reject(args.mission, args.lease_token, args.reason)); return 0
def _fail(args): _print(_inbox(args).fail(args.mission, args.lease_token, args.reason, retryable=args.retryable)); return 0
def _retry(args): _print(_inbox(args).retry(args.mission, actor=args.actor, reason=args.reason)); return 0
def _signal(args): _print(_inbox(args).record_signal(args.mission, event_id=args.event_id, kind=args.kind, actor=args.actor, detail=_load(args.detail))); return 0
def _get(args): _print(_inbox(args).get(args.mission)); return 0
def _list(args):
    status = MissionStatus(args.status) if args.status else None; _print(_inbox(args).list(status=status, owner_scope=args.owner, limit=args.limit)); return 0
def _inventory(args): _print(_inbox(args).inventory()); return 0
def _agents(args): _print(_inbox(args).list_agents(limit=args.limit)); return 0
def _recover(args): _print(_inbox(args).recover_expired()); return 0
def _probe(args):
    if args.kind == "liveness": result = liveness()
    elif args.kind == "readiness": result = readiness(args.db)
    else: result = functional_counter_proof()
    _print(result); return 0 if result["ok"] else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-inbox", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    def command(name, function, help):
        item = commands.add_parser(name, help=help); item.add_argument("--db", type=Path, required=True); item.set_defaults(run=function); return item
    command("init", _init, "initialize the SQLite inbox")
    item = command("register", _register, "register or update an agent"); item.add_argument("--input", type=Path, required=True)
    item = command("enqueue", _enqueue, "idempotently enqueue a mission"); item.add_argument("--input", type=Path, required=True)
    item = command("claim", _claim, "atomically claim a compatible mission"); item.add_argument("--agent", required=True); item.add_argument("--lease-seconds", type=int, default=60)
    item = command("heartbeat", _heartbeat, "renew an active lease"); item.add_argument("--mission", required=True); item.add_argument("--lease-token", required=True); item.add_argument("--lease-seconds", type=int, default=60)
    item = command("complete", _complete, "complete with mandatory evidence"); item.add_argument("--mission", required=True); item.add_argument("--lease-token", required=True); item.add_argument("--evidence", type=Path, required=True)
    for name, function in (("wait", _wait), ("reject", _reject)):
        item = command(name, function, f"mark a running mission {name}"); item.add_argument("--mission", required=True); item.add_argument("--lease-token", required=True); item.add_argument("--reason", required=True)
    item = command("fail", _fail, "fail or retry a running mission"); item.add_argument("--mission", required=True); item.add_argument("--lease-token", required=True); item.add_argument("--reason", required=True); item.add_argument("--retryable", action=argparse.BooleanOptionalAction, default=True)
    item = command("retry", _retry, "manually retry waiting/failed work"); item.add_argument("--mission", required=True); item.add_argument("--actor", required=True); item.add_argument("--reason", required=True)
    item = command("signal", _signal, "record a disagreement or escalation"); item.add_argument("--mission", required=True); item.add_argument("--event-id", required=True); item.add_argument("--kind", choices=("disagreement", "escalation"), required=True); item.add_argument("--actor", required=True); item.add_argument("--detail", type=Path, required=True)
    item = command("get", _get, "get a mission and its events"); item.add_argument("--mission", required=True)
    item = command("list", _list, "list bounded missions"); item.add_argument("--status", choices=tuple(status.value for status in MissionStatus)); item.add_argument("--owner"); item.add_argument("--limit", type=int, default=100)
    command("inventory", _inventory, "show canonical status and signal counts")
    item = command("agents", _agents, "list the bounded capability registry"); item.add_argument("--limit", type=int, default=100)
    command("recover", _recover, "recover expired leases exactly once")
    probe = commands.add_parser("probe", help="run an offline health probe"); probe.add_argument("kind", choices=("liveness", "readiness", "functional")); probe.add_argument("--db", type=Path, default=Path("agent-inbox.sqlite3")); probe.set_defaults(run=_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv); return int(args.run(args))
    except NoMissionAvailable as exc:
        print(canonical_json({"error": type(exc).__name__, "message": str(exc), "success": False}), file=sys.stderr); return 2
    except (ContractError, InboxError, OSError, json.JSONDecodeError) as exc:
        print(canonical_json({"error": type(exc).__name__, "message": str(exc), "success": False}), file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
