"""Command-line interface for the local software factory."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from .core import evaluate
from .evidence import verify_export
from .engine import FactoryEngine
from .graph import topological_order
from .models import MAX_SPEC_BYTES, FactorySpec, SpecError
from .state import RunState
from .store import FactoryStore, StoreError


COMMANDS = {
    "init",
    "validate",
    "plan",
    "run",
    "status",
    "replay",
    "export",
    "verify",
    "kill",
    "approval-request",
    "approval-decide",
    "template-compile",
    "template-plan",
    "template-run",
}
MAX_EXPORT_BYTES = 64 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_text_bounded(path: Path, *, maximum: int, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if path.stat().st_size > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes")
    return path.read_text(encoding="utf-8")


EXAMPLE_SPEC: dict[str, Any] = {
    "schema_version": 1,
    "name": "synthetic-python-component",
    "workspace": ".factory/workspace",
    "budget": {
        "max_tasks": 10,
        "max_attempts": 8,
        "max_wall_seconds": 120,
        "max_output_bytes": 65536,
        "default_task_timeout_seconds": 15,
        "lease_seconds": 30,
        "retry_base_seconds": 0,
        "retry_cap_seconds": 0,
        "default_max_attempts": 2,
    },
    "tasks": [
        {
            "id": "build",
            "owner": "local-builder",
            "description": "Create a deterministic synthetic module.",
            "command": [
                "python",
                "-c",
                "from pathlib import Path; p=Path('src/calculator.py'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('def add(a, b):\\n    return a + b\\n',encoding='utf-8')",
            ],
            "owned_paths": ["src/calculator.py"],
            "artifacts": ["src/calculator.py"],
            "tests": [
                {
                    "name": "syntax",
                    "command": [
                        "python",
                        "-c",
                        "from pathlib import Path; compile(Path('src/calculator.py').read_text(encoding='utf-8'),'src/calculator.py','exec')",
                    ],
                }
            ],
        },
        {
            "id": "contract-test",
            "owner": "local-verifier",
            "description": "Exercise the public behavior without network access.",
            "depends_on": ["build"],
            "command": [
                "python",
                "-c",
                "from pathlib import Path; ns={}; exec(Path('src/calculator.py').read_text(encoding='utf-8'),ns); assert ns['add'](20,22)==42; p=Path('reports/contract.txt'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('PASS\\n',encoding='utf-8')",
            ],
            "owned_paths": ["reports/contract.txt"],
            "artifacts": ["reports/contract.txt"],
        },
    ],
}


def _render(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _load_spec(path: Path) -> FactorySpec:
    return FactorySpec.from_json(
        _read_text_bounded(path, maximum=MAX_SPEC_BYTES, label="factory specification")
    )


def _database_path(spec_path: Path | None, value: Path | None) -> Path:
    if value is not None:
        return value
    base = spec_path.resolve().parent if spec_path else Path.cwd()
    return base / ".factory" / "factory.sqlite3"


def _spec_summary(spec: FactorySpec) -> dict[str, Any]:
    canonical = spec.canonical_json()
    return {
        "valid": True,
        "schema_version": spec.schema_version,
        "name": spec.name,
        "task_count": len(spec.tasks),
        "topological_order": list(topological_order(spec.tasks)),
        "spec_sha256": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _emit_json(rendered: str, *, file=None) -> None:
    """Emit the already rendered native JSON without changing its bytes or stream."""
    print(rendered, end="", file=file)


def _legacy(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ai-software-factory")
    parser.add_argument("record", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = json.loads(
        _read_text_bounded(args.record, maximum=MAX_SPEC_BYTES, label="legacy record"),
        object_pairs_hook=_unique_object,
    )
    if not isinstance(raw, dict):
        raise SpecError("legacy record must be a JSON object")
    result = evaluate(raw)
    rendered = _render(result)
    if args.output:
        _atomic_write(args.output, rendered)
    else:
        _emit_json(rendered)
    return 0 if result["status"] == "passed" else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-software-factory",
        description="Durable, evidence-first local DAG execution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a synthetic starter spec")
    init.add_argument("directory", nargs="?", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true")

    validate = subparsers.add_parser("validate", help="strictly validate a spec")
    validate.add_argument("spec", type=Path)

    plan = subparsers.add_parser("plan", help="persist an idempotent run plan")
    plan.add_argument("spec", type=Path)
    plan.add_argument("--db", type=Path)
    plan.add_argument("--idempotency-key")

    run = subparsers.add_parser("run", help="plan/resume and execute a spec")
    run.add_argument("spec", type=Path)
    run.add_argument("--db", type=Path)
    run.add_argument("--idempotency-key")
    run.add_argument("--worker-id", default="cli-worker")

    for command in ("template-compile", "template-plan", "template-run"):
        child = subparsers.add_parser(command, help="compile an explicit catalog template through the native engine")
        child.add_argument("catalog", type=Path)
        child.add_argument("--template", required=True)
        child.add_argument("--bindings", type=Path, required=True)
        if command == "template-compile":
            child.add_argument("--output", type=Path, help="create a new compilation JSON; never overwrite")
        else:
            child.add_argument("--db", type=Path)
            child.add_argument("--idempotency-key")
            if command == "template-run":
                child.add_argument("--worker-id", default="cli-worker")

    verify = subparsers.add_parser("verify", help="verify an exported bundle offline")
    verify.add_argument("export_file", type=Path)

    for command in ("approval-request", "approval-decide"):
        child = subparsers.add_parser(command, help="inspect/record a declarative attempt approval")
        child.add_argument("--db", type=Path, required=True)
        child.add_argument("--run-id", required=True)
        child.add_argument("--task-id", required=True)
        if command == "approval-decide":
            child.add_argument("--attempt", type=int, required=True)
            child.add_argument("--request-sha256", required=True)
            child.add_argument("--decision", choices=("approved", "rejected"), required=True)
            child.add_argument("--decided-by", required=True)
            child.add_argument("--expires-at", type=float, required=True, help="epoch seconds; at most 86400 seconds ahead")
            child.add_argument("--decision-id", required=True)

    for command in ("status", "replay", "export", "kill"):
        child = subparsers.add_parser(command)
        child.add_argument("--db", type=Path, required=True)
        child.add_argument("--run-id", required=True)
        if command == "export":
            child.add_argument("--output", type=Path)
        if command == "kill":
            child.add_argument("--reason", required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command in {"template-compile", "template-plan", "template-run"}:
        from .templates import compile_template, read_json, MAX_CATALOG_BYTES, MAX_BINDINGS_BYTES
        from .template_inputs import input_path, read_input
        path = input_path(args.catalog)
        catalog = read_json(read_input(path, maximum=MAX_CATALOG_BYTES), maximum=MAX_CATALOG_BYTES)
        bindings = read_json(read_input(args.bindings, maximum=MAX_BINDINGS_BYTES), maximum=MAX_BINDINGS_BYTES)
        compiled = compile_template(catalog, args.template, bindings)
        if args.command == "template-compile":
            rendered = _render(compiled.to_dict())
            if args.output:
                # This command never replaces an earlier compilation or spec.
                with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(rendered)
            else:
                _emit_json(rendered)
            return 0
        database = _database_path(path, args.db)
        store = FactoryStore(database)
        engine = FactoryEngine(store, base_directory=path.parent)
        run_id = engine.plan_template(catalog, args.template, bindings, idempotency_key=args.idempotency_key)
        if args.command == "template-plan":
            _emit_json(_render({"run_id":run_id, "database":str(database.resolve()),
                           "origin_sha256":compiled.to_dict()["origin_sha256"], "status":store.snapshot(run_id)}))
            return 0
        result = engine.run(run_id, worker_id=args.worker_id)
        _emit_json(_render(store.snapshot(run_id)))
        if result.waiting_for_approval or result.waiting_for_quota:
            return 3
        return 0 if result.state == RunState.SUCCEEDED else 2

    if args.command == "init":
        target = args.directory.resolve()
        path = target / "factory.json"
        if path.exists() and not args.force:
            raise FileExistsError(f"refusing to overwrite existing {path}")
        target.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, _render(EXAMPLE_SPEC))
        _emit_json(_render(
                {"created": str(path), "next": f"ai-software-factory run {path}"}
            ))
        return 0

    if args.command in {"validate", "plan", "run"}:
        spec_path = args.spec.resolve()
        spec = _load_spec(spec_path)
        if args.command == "validate":
            _emit_json(_render(_spec_summary(spec)))
            return 0
        database = _database_path(spec_path, args.db)
        store = FactoryStore(database)
        engine = FactoryEngine(store, base_directory=spec_path.parent)
        run_id = engine.plan(spec, idempotency_key=args.idempotency_key)
        if args.command == "plan":
            _emit_json(_render(
                    {
                        "run_id": run_id,
                        "database": str(database.resolve()),
                        "status": store.snapshot(run_id),
                    }
                ))
            return 0
        result = engine.run(run_id, worker_id=args.worker_id)
        _emit_json(_render(store.snapshot(run_id)))
        if result.waiting_for_approval or result.waiting_for_quota:
            return 3
        return 0 if result.state == RunState.SUCCEEDED else 2

    if args.command == "verify":
        exported = json.loads(
            _read_text_bounded(
                args.export_file,
                maximum=MAX_EXPORT_BYTES,
                label="evidence export",
            ),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(exported, dict):
            raise ValueError("export must be a JSON object")
        valid, issues = verify_export(exported)
        _emit_json(_render({"valid": valid, "issues": list(issues)}))
        return 0 if valid else 2

    store = FactoryStore(args.db, create=False)
    if args.command == "approval-request":
        value = store.approval_request(args.run_id, args.task_id)
    elif args.command == "approval-decide":
        value = store.record_approval(args.run_id, args.task_id, attempt=args.attempt,
            request_sha256=args.request_sha256, decision=args.decision,
            decided_by=args.decided_by, expires_at=args.expires_at, decision_id=args.decision_id)
    elif args.command == "status":
        value: object = store.snapshot(args.run_id)
    elif args.command == "replay":
        value = {"run_id": args.run_id, "events": store.replay(args.run_id)}
    elif args.command == "export":
        value = store.export(args.run_id)
        rendered = _render(value)
        if args.output:
            _atomic_write(args.output, rendered)
        else:
            _emit_json(rendered)
        return 0
    elif args.command == "kill":
        store.activate_kill_switch(args.run_id, reason=args.reason)
        value = store.snapshot(args.run_id)
    else:  # pragma: no cover - argparse makes this unreachable
        raise AssertionError(args.command)
    _emit_json(_render(value))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if (
            arguments
            and arguments[0] not in COMMANDS
            and not arguments[0].startswith("-")
        ):
            return _legacy(arguments)
        return _dispatch(_parser().parse_args(arguments))
    except (SpecError, StoreError, OSError, ValueError, json.JSONDecodeError) as exc:
        _emit_json(_render({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
