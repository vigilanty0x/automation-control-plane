from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable, Sequence

from ._common import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, ValidationError, blocked, canonical_json, strict_loads
from .circuits import simulate_circuit
from .consumers import inventory_consumers
from .context import plan_context
from .inbox import project_inbox
from .inventory import inventory
from .quota import simulate_quota
from .rollback import rehearse_rollback
from .routing import evaluate_routing
from .sessions import record_session, verify_session

_COMMANDS: dict[str, tuple[str, Callable[[Any], dict[str, Any]]]] = {
    "route": ("routing", evaluate_routing),
    "context": ("context_budget", plan_context),
    "quota": ("quota_simulation", simulate_quota),
    "session-record": ("session_record", record_session),
    "session-verify": ("session_verify", verify_session),
    "circuit": ("circuit_simulation", simulate_circuit),
    "inbox": ("operator_inbox", project_inbox),
    "consumers": ("consumer_inventory", inventory_consumers),
    "rollback": ("rollback_rehearsal", rehearse_rollback),
}


def _read_input(path: str) -> Any:
    if path == "-":
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        file_path = Path(path)
        if not file_path.is_file():
            raise ValidationError(f"input path is not a file: {path}")
        if file_path.stat().st_size > MAX_INPUT_BYTES:
            raise ValidationError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        data = file_path.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise ValidationError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("input must be UTF-8") from exc
    return strict_loads(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m automation_control_plane.agentops",
        description="Bounded AgentOps contract rehearsal on top of Automation Control Plane.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory", help="emit the exact source and disposition inventory")
    for command in _COMMANDS:
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--input",
            required=True,
            help="UTF-8 JSON file, or - for standard input",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inventory":
        result = inventory()
    else:
        kind, handler = _COMMANDS[args.command]
        try:
            payload = _read_input(args.input)
            result = handler(payload)
        except (OSError, ValidationError) as exc:
            result = blocked(kind, {}, exc)
    rendered = canonical_json(result) + "\n"
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        fallback = blocked("cli", {}, RuntimeError("output exceeds configured bound"))
        rendered = json.dumps(fallback, sort_keys=True, separators=(",", ":")) + "\n"
    sys.stdout.write(rendered)
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
