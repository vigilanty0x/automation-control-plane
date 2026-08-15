from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from model_router.models import (
    AgentProfile,
    EvidenceBundle,
    MissionState,
    RiskLevel,
    TaskRequest,
)
from model_router.registry import CapabilityRegistry
from model_router.router import ModelRouter
from model_router.service import ModelRouterService
from model_router.state_machine import InvalidTransition
from model_router.store import MissionNotFound, SQLiteMissionStore


MAX_INPUT_BYTES = 1_000_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model-router",
        description="Evidence-first routing and persistent mission queue.",
    )
    parser.add_argument("--version", action="version", version="model-router 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    route = commands.add_parser("route", help="Score one task without writing a database")
    _file_inputs(route)

    enqueue = commands.add_parser("enqueue", help="Route and idempotently enqueue one task")
    _database(enqueue)
    _file_inputs(enqueue)

    claim = commands.add_parser("claim", help="Claim the oldest eligible queued mission")
    _database(claim)
    claim.add_argument("--worker", required=True)
    claim.add_argument("--lease-seconds", type=int, default=300)

    approve = commands.add_parser("approve", help="Record human approval for a gated mission")
    _database(approve)
    approve.add_argument("--mission", required=True)
    approve.add_argument("--actor", required=True)

    transition = commands.add_parser("transition", help="Apply an explicit state transition")
    _database(transition)
    transition.add_argument("--mission", required=True)
    transition.add_argument("--to", required=True, choices=[state.value for state in MissionState])
    transition.add_argument("--actor", required=True)
    transition.add_argument("--reason", required=True)
    transition.add_argument("--evidence")

    retry = commands.add_parser("retry", help="Retry a failed mission inside its attempt budget")
    _database(retry)
    retry.add_argument("--mission", required=True)
    retry.add_argument("--actor", required=True)

    recover = commands.add_parser("recover", help="Expose expired worker leases as failures")
    _database(recover)
    recover.add_argument("--actor", required=True)
    recover.add_argument("--now", help="Optional ISO-8601 recovery time for deterministic jobs")

    inspect = commands.add_parser("inspect", help="Show one mission and its complete event trail")
    _database(inspect)
    inspect.add_argument("--mission", required=True)

    list_command = commands.add_parser("list", help="List missions, optionally by state")
    _database(list_command)
    list_command.add_argument("--state", choices=[state.value for state in MissionState])

    metrics = commands.add_parser("metrics", help="Show durable operational metrics")
    _database(metrics)

    demo = commands.add_parser("demo", help="Run a complete synthetic mission journey")
    _database(demo)
    return parser


def _database(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, help="SQLite database path")


def _file_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agents", required=True, help="Agent registry JSON file")
    parser.add_argument("--task", required=True, help="Task request JSON file")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _dispatch(args)
    except MissionNotFound as exc:
        _error("mission_not_found", str(exc.args[0]))
        return 3
    except InvalidTransition as exc:
        _error("invalid_transition", str(exc))
        return 4
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _error("invalid_input", str(exc))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "route":
        registry = _load_registry(args.agents)
        request = TaskRequest.from_dict(_load_json(args.task, expected=dict))
        return ModelRouter(registry).route(request).to_dict()
    if args.command == "demo":
        return _demo(args.db)

    with SQLiteMissionStore(args.db) as store:
        if args.command == "enqueue":
            registry = _load_registry(args.agents)
            request = TaskRequest.from_dict(_load_json(args.task, expected=dict))
            return ModelRouterService(ModelRouter(registry), store).submit(request).to_dict()
        if args.command == "claim":
            mission = store.claim(args.worker, lease_seconds=args.lease_seconds)
            return mission.to_dict() if mission else None
        if args.command == "approve":
            return store.approve(args.mission, actor=args.actor).to_dict()
        if args.command == "transition":
            evidence = (
                EvidenceBundle.from_dict(_load_json(args.evidence, expected=dict))
                if args.evidence
                else None
            )
            return store.transition(
                args.mission,
                MissionState(args.to),
                actor=args.actor,
                reason=args.reason,
                evidence=evidence,
            ).to_dict()
        if args.command == "retry":
            return store.retry(args.mission, actor=args.actor).to_dict()
        if args.command == "recover":
            return [
                mission.to_dict()
                for mission in store.recover_expired(actor=args.actor, now=args.now)
            ]
        if args.command == "inspect":
            mission = store.get(args.mission)
            return {
                "mission": mission.to_dict(),
                "events": [event.to_dict() for event in store.events(args.mission)],
            }
        if args.command == "list":
            state = MissionState(args.state) if args.state else None
            return [mission.to_dict() for mission in store.list(state=state)]
        if args.command == "metrics":
            return store.metrics()
    raise ValueError(f"unsupported command: {args.command}")


def _load_registry(path: str) -> CapabilityRegistry:
    payload = _load_json(path, expected=list)
    return CapabilityRegistry(AgentProfile.from_dict(item) for item in payload)


def _load_json(path: str, *, expected: type[list] | type[dict]) -> Any:
    source = Path(path)
    size = source.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} byte limit: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} in {source}")
    return payload


def _error(kind: str, message: str) -> None:
    print(json.dumps({"error": kind, "message": message}, sort_keys=True), file=sys.stderr)


def _demo(db_path: str) -> dict[str, Any]:
    registry = CapabilityRegistry(
        [
            AgentProfile(
                agent_id="demo-forge",
                owner="demo-platform-team",
                capabilities=frozenset({"python", "testing", "documentation"}),
                permissions=frozenset({"read", "write"}),
                cost_per_1k_tokens_usd=0.015,
                p95_latency_ms=800,
                context_window_tokens=64_000,
                historical_success_rate=0.96,
                max_concurrency=2,
            )
        ]
    )
    request = TaskRequest(
        task_id="demo-task-001",
        idempotency_key="model-router-demo-v1",
        title="Produce a bounded synthetic routing report",
        required_capabilities=frozenset({"python", "testing"}),
        required_permissions=frozenset({"read", "write"}),
        budget_usd=2.0,
        max_latency_ms=2_000,
        context_tokens=10_000,
        scope=("artifacts/demo-report.json",),
        acceptance_criteria=("demo tests pass", "demo report exists"),
        max_attempts=2,
    )
    evidence = EvidenceBundle(
        commit_sha="demo000000000000000000000000000000000001",
        tests=("synthetic-demo-check:pass",),
        artifacts=("artifacts/demo-report.json",),
        criteria={"demo tests pass": True, "demo report exists": True},
        produced_by="demo-forge",
        notes=("No external service or private account was used.",),
    )
    with SQLiteMissionStore(db_path) as store:
        submission = ModelRouterService(ModelRouter(registry), store).submit(request)
        mission = submission.mission
        if mission.state is MissionState.QUEUED:
            claimed = store.claim("demo-forge")
            if claimed is not None:
                mission = store.transition(
                    claimed.mission_id,
                    MissionState.DONE,
                    actor="demo-forge",
                    reason="synthetic demo gates passed",
                    evidence=evidence,
                )
        return {
            "created": submission.created,
            "mission": mission.to_dict(),
            "events": [event.to_dict() for event in store.events(mission.mission_id)],
            "metrics": store.metrics(),
        }
