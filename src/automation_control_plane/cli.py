"""Machine-readable CLI for simulation and durable control-plane operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence

from .core import run as simulation_run
from .engine import ControlPlane
from .http_api import serve
from .models import WorkflowDefinition, parse_json
from .storage import DATABASE_SCHEMA_VERSION, ControlPlaneStore


MAX_INPUT_BYTES = 25_000_000
MAX_ERROR_MESSAGE = 512
COMMANDS = {
    "init", "register", "submit", "list", "show", "approve", "reject", "cancel",
    "worker", "audit", "backup", "restore", "demo", "kill", "reconcile", "role", "serve",
}


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep invalid command syntax on the bounded JSON error path."""

    def error(self, message: str) -> None:
        raise ValueError(f"invalid command arguments: {message}")


def _read_json(path: str | None, *, stdin_when_none: bool = False) -> Any:
    if path is None:
        if not stdin_when_none:
            return {}
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"input path must be a file: {source}")
        with source.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input byte limit exceeded")
    try:
        return parse_json(raw.decode("utf-8"), label="input JSON", maximum_bytes=MAX_INPUT_BYTES)
    except UnicodeDecodeError as exc:
        raise ValueError("input must be UTF-8 JSON") from exc


def _emit(value: Any, *, success: bool = True) -> None:
    print(json.dumps({"success": success, "result": value} if success else value, sort_keys=True, allow_nan=False))


def _database(args: argparse.Namespace, *, initialize: bool = False) -> tuple[ControlPlaneStore, ControlPlane]:
    store = ControlPlaneStore(args.db)
    if initialize:
        store.initialize()
    else:
        store.ensure_initialized()
    return store, ControlPlane(store)


def _add_database(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="control-plane.db", help="SQLite database path")


def _add_principal(parser: argparse.ArgumentParser, default: str = "admin") -> None:
    parser.add_argument("--principal", default=default, help="local authenticated principal asserted by the caller")


def _build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="automation-control-plane", description="Governed offline automation control plane")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a least-privilege SQLite database"); _add_database(init)
    register = sub.add_parser("register", help="register an immutable workflow JSON version")
    _add_database(register); _add_principal(register); register.add_argument("definition"); register.add_argument("--inactive", action="store_true")
    submit = sub.add_parser("submit", help="submit an idempotent job")
    _add_database(submit); _add_principal(submit); submit.add_argument("workflow_id"); submit.add_argument("--version", type=int)
    submit.add_argument("--trigger", choices=("manual", "webhook", "scheduled"), default="manual")
    submit.add_argument("--event"); submit.add_argument("--interval-seconds", type=int); submit.add_argument("--payload")
    submit.add_argument("--idempotency-key", required=True); submit.add_argument("--budget", type=int); submit.add_argument("--deadline-seconds", type=int); submit.add_argument("--dry-run", action="store_true")
    listing = sub.add_parser("list", help="list workflows, jobs, events, outbox records, or kill switches")
    _add_database(listing); _add_principal(listing); listing.add_argument("kind", choices=("workflows", "jobs", "events", "outbox", "kill-switches"))
    listing.add_argument("--state"); listing.add_argument("--after", type=int, default=0); listing.add_argument("--limit", type=int, default=100)
    show = sub.add_parser("show", help="show one job and all steps/approvals"); _add_database(show); _add_principal(show); show.add_argument("job_id")
    for name in ("approve", "reject"):
        item = sub.add_parser(name, help=f"{name} a bound step approval"); _add_database(item); _add_principal(item)
        item.add_argument("job_id"); item.add_argument("step_id"); item.add_argument("--reason", required=True); item.add_argument("--job-version", type=int); item.add_argument("--step-version", type=int)
    cancel = sub.add_parser("cancel", help="cancel a nonterminal job"); _add_database(cancel); _add_principal(cancel)
    cancel.add_argument("job_id"); cancel.add_argument("--reason", required=True); cancel.add_argument("--version", type=int)
    worker = sub.add_parser("worker", help="execute registered safe handlers"); _add_database(worker); _add_principal(worker, "worker")
    worker.add_argument("--lease-seconds", type=int, default=60); worker.add_argument("--max-steps", type=int, default=1); worker.add_argument("--poll-seconds", type=float, default=0); worker.add_argument("--forever", action="store_true")
    audit = sub.add_parser("audit", help="verify the complete tamper-evident event chain"); _add_database(audit); _add_principal(audit)
    backup = sub.add_parser("backup", help="create a SQLite online backup"); _add_database(backup); backup.add_argument("destination")
    restore = sub.add_parser("restore", help="integrity-check and restore a backup"); _add_database(restore); restore.add_argument("source"); restore.add_argument("--force", action="store_true")
    demo = sub.add_parser("demo", help="run a complete synthetic governed DAG"); _add_database(demo)
    kill = sub.add_parser("kill", help="enable or disable a global/workflow kill switch"); _add_database(kill); _add_principal(kill)
    kill.add_argument("scope", choices=("global", "workflow")); kill.add_argument("scope_id", nargs="?", default=""); kill.add_argument("--enable", action="store_true"); kill.add_argument("--disable", action="store_true"); kill.add_argument("--reason", required=True); kill.add_argument("--version", type=int)
    reconcile = sub.add_parser("reconcile", help="recover leases and reconcile job/outbox state"); _add_database(reconcile); _add_principal(reconcile)
    role = sub.add_parser("role", help="assign a built-in least-privilege role"); _add_database(role); _add_principal(role)
    role.add_argument("principal_name"); role.add_argument("role_name", choices=("admin", "operator", "approver", "dispatcher", "worker", "viewer"))
    server = sub.add_parser("serve", help="serve the loopback-only read-only dashboard"); _add_database(server); _add_principal(server)
    server.add_argument("--host", default="127.0.0.1"); server.add_argument("--port", type=int, default=8787)
    return parser


def _trigger(args: argparse.Namespace) -> dict[str, Any]:
    if args.trigger == "manual":
        if args.event is not None or args.interval_seconds is not None: raise ValueError("manual trigger accepts neither event nor interval")
        return {"type": "manual"}
    if args.trigger == "webhook":
        if args.event is None or args.interval_seconds is not None: raise ValueError("webhook trigger requires --event only")
        return {"type": "webhook", "event": args.event}
    if args.interval_seconds is None or args.event is not None: raise ValueError("scheduled trigger requires --interval-seconds only")
    return {"type": "scheduled", "interval_seconds": args.interval_seconds}


def _demo_workflow() -> dict[str, Any]:
    retry = {"max_attempts": 3, "initial_delay_seconds": 1, "multiplier": 2, "max_delay_seconds": 30}
    return {
        "schema_version": "1.0", "workflow_id": "synthetic-release", "version": 1,
        "description": "Synthetic, offline release evidence flow.", "budget_units": 10, "default_deadline_seconds": 900,
        "triggers": [{"type": "manual"}, {"type": "webhook", "event": "synthetic.release.requested"}, {"type": "scheduled", "interval_seconds": 86400}],
        "steps": [
            {"id": "prepare", "handler": "emit", "depends_on": [], "input": {"artifact": "synthetic-report"}, "required_capability": "handler:emit", "approval": "none", "estimated_cost": 1, "timeout_seconds": 30, "retry": retry},
            {"id": "verify", "handler": "assert.equals", "depends_on": ["prepare"], "input": {"actual": "verified", "expected": "verified"}, "required_capability": "handler:assert.equals", "approval": "none", "estimated_cost": 0, "timeout_seconds": 30, "retry": retry},
            {"id": "release", "handler": "noop", "depends_on": ["verify"], "input": {}, "required_capability": "handler:noop", "approval": "required", "estimated_cost": 0, "timeout_seconds": 30, "retry": retry},
        ],
    }


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "init":
        store, _ = _database(args, initialize=True); return {"database": str(store.path), "schema_version": DATABASE_SCHEMA_VERSION, "default_admin": "admin"}
    if args.command == "restore":
        restored = ControlPlaneStore.restore(args.source, args.db, force=args.force); return {"database": str(restored.path), "audit": restored.verify_audit()}
    store, control = _database(args)
    if args.command == "register": return control.register_workflow(WorkflowDefinition.from_dict(_read_json(args.definition)), principal=args.principal, activate=not args.inactive)
    if args.command == "submit":
        return control.submit(args.workflow_id, principal=args.principal, trigger=_trigger(args), idempotency_key=args.idempotency_key,
                              payload=_read_json(args.payload) if args.payload else {}, workflow_version=args.version,
                              budget_units=args.budget, deadline_seconds=args.deadline_seconds, dry_run=args.dry_run)
    if args.command == "list":
        if args.kind == "workflows": return control.list_workflows(principal=args.principal, limit=args.limit)
        if args.kind == "jobs": return control.list_jobs(principal=args.principal, state=args.state, limit=args.limit)
        if args.kind == "events": return control.list_events(principal=args.principal, after=args.after, limit=args.limit)
        if args.kind == "outbox": return control.list_outbox(principal=args.principal, state=args.state, limit=args.limit)
        return control.list_kill_switches(principal=args.principal, limit=args.limit)
    if args.command == "show": return control.show_job(args.job_id, principal=args.principal)
    if args.command in {"approve", "reject"}:
        return control.decide_approval(args.job_id, args.step_id, principal=args.principal,
            decision="approved" if args.command == "approve" else "rejected", reason=args.reason,
            expected_job_version=args.job_version, expected_step_version=args.step_version)
    if args.command == "cancel": return control.cancel_job(args.job_id, principal=args.principal, reason=args.reason, expected_version=args.version)
    if args.command == "worker":
        if args.max_steps < 1 or args.max_steps > 100_000 or args.poll_seconds < 0 or args.poll_seconds > 60: raise ValueError("worker bounds are invalid")
        results = []
        while args.forever or len(results) < args.max_steps:
            result = control.execute_once(worker=args.principal, lease_seconds=args.lease_seconds); results.append(result)
            if result["status"] == "idle":
                if not args.forever or args.poll_seconds == 0: break
                time.sleep(args.poll_seconds)
        return {"worker": args.principal, "results": results}
    if args.command == "audit": return control.verify_audit(principal=args.principal)
    if args.command == "backup": return {"backup": str(store.backup(args.destination))}
    if args.command == "kill":
        if args.enable == args.disable: raise ValueError("choose exactly one of --enable or --disable")
        return control.set_kill_switch(scope=args.scope, scope_id=args.scope_id, enabled=args.enable, reason=args.reason, principal=args.principal, expected_version=args.version)
    if args.command == "reconcile": return {"recovery": control.recover(principal=args.principal), "reconciliation": control.reconcile(principal=args.principal)}
    if args.command == "role": return control.assign_role(args.principal_name, args.role_name, principal=args.principal)
    if args.command == "demo":
        control.assign_role("demo-worker", "worker", principal="admin"); control.assign_role("demo-approver", "approver", principal="admin")
        control.register_workflow(_demo_workflow(), principal="admin")
        job = control.submit("synthetic-release", principal="admin", trigger={"type": "manual"}, idempotency_key=f"demo-{time.time_ns()}", payload={"synthetic": True}, dry_run=True)
        results = []
        for _ in range(10):
            result = control.execute_once(worker="demo-worker"); results.append(result)
            current = control.show_job(job["job_id"], principal="admin")
            waiting = [step for step in current["steps"] if step["state"] == "waiting_approval"]
            if waiting: control.decide_approval(job["job_id"], waiting[0]["step_id"], principal="demo-approver", decision="approved", reason="synthetic demo approval")
            if current["state"] in {"completed", "failed", "cancelled"}: break
        return {"job": control.show_job(job["job_id"], principal="admin"), "worker_results": results, "audit": control.verify_audit(principal="admin")}
    if args.command == "serve": serve(control, host=args.host, port=args.port, principal=args.principal); return None
    raise ValueError("unknown command")


def _legacy(argv: Sequence[str]) -> bool:
    return not argv or (argv[0] not in COMMANDS and argv[0] not in {"-h", "--help"})


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if _legacy(arguments):
            if len(arguments) > 1: raise ValueError("legacy simulation expects at most one input path")
            _emit(simulation_run(_read_json(arguments[0] if arguments else None, stdin_when_none=True)))
        else:
            result = _dispatch(_build_parser().parse_args(arguments))
            if result is not None: _emit(result)
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:MAX_ERROR_MESSAGE]
        print(json.dumps({"success": False, "error": type(exc).__name__, "message": message}, sort_keys=True, allow_nan=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
