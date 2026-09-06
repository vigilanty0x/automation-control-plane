from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .engine import AgentWorktreeService, GitCommandError, SafetyError
from .models import EvidenceBundle, MissionRequest, MissionState
from .state_machine import InvalidTransition
from .store import MissionNotFound, SQLiteMissionStore


MAX_JSON_BYTES = 1_048_576


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds the 1 MB limit")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {source}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON input must contain an object")
    return value


def _emit(value: object, *, stream: Any = None) -> None:
    print(json.dumps(value, indent=2, sort_keys=True), file=sys.stdout if stream is None else stream)


def _common_mission(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, help="SQLite state file")
    parser.add_argument("--mission", required=True, help="mission identifier")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees",
        description="Allocate isolated Git worktrees with ownership and evidence gates.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser("register", help="idempotently register a mission")
    register.add_argument("--db", required=True)
    register.add_argument("--repo", required=True)
    register.add_argument("--worktree-root", required=True)
    register.add_argument("--request", required=True)

    provision = commands.add_parser("provision", help="create or resume the mission worktree")
    _common_mission(provision)
    provision.add_argument("--actor", required=True)

    complete = commands.add_parser("complete", help="verify evidence and mark done")
    _common_mission(complete)
    complete.add_argument("--actor", required=True)
    complete.add_argument("--evidence", required=True)

    fail = commands.add_parser("fail", help="record a visible failure")
    _common_mission(fail)
    fail.add_argument("--actor", required=True)
    fail.add_argument("--reason", required=True)

    retry = commands.add_parser("retry", help="requeue without creating a branch")
    _common_mission(retry)
    retry.add_argument("--actor", required=True)

    wait = commands.add_parser("wait", help="record a visible wait or escalation")
    _common_mission(wait)
    wait.add_argument("--actor", required=True)
    wait.add_argument("--reason", required=True)

    resume = commands.add_parser("resume", help="resume a waiting mission")
    _common_mission(resume)
    resume.add_argument("--actor", required=True)
    resume.add_argument("--reason", required=True)

    intervene = commands.add_parser("intervene", help="record a human intervention")
    _common_mission(intervene)
    intervene.add_argument("--actor", required=True)
    intervene.add_argument("--reason", required=True)

    cleanup = commands.add_parser("cleanup", help="remove only clean integrated work")
    _common_mission(cleanup)
    cleanup.add_argument("--actor", required=True)
    cleanup.add_argument("--keep-branch", action="store_true")

    inspect = commands.add_parser("inspect", help="show mission and ordered evidence")
    _common_mission(inspect)

    listing = commands.add_parser("list", help="list registered missions")
    listing.add_argument("--db", required=True)
    listing.add_argument("--state", choices=[state.value for state in MissionState])

    recover = commands.add_parser("recover", help="expose missing worktrees as failures")
    recover.add_argument("--db", required=True)
    recover.add_argument("--actor", required=True)

    audit = commands.add_parser("audit", help="compare registry with git worktree state")
    audit.add_argument("--db", required=True)
    audit.add_argument("--repo", required=True)

    metrics = commands.add_parser("metrics", help="show durable operating metrics")
    metrics.add_argument("--db", required=True)

    demo = commands.add_parser("demo", help="run a complete synthetic Git journey")
    demo.add_argument("--workspace", required=True)
    return parser


def _run_demo(workspace: Path) -> dict[str, Any]:
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError("demo workspace must be absent or empty")
    workspace.mkdir(parents=True, exist_ok=True)
    repo = workspace / "repo"
    repo.mkdir()
    AgentWorktreeService._git(repo, "init", "-b", "main")
    AgentWorktreeService._git(repo, "config", "user.name", "Synthetic Demo")
    AgentWorktreeService._git(repo, "config", "user.email", "demo@example.test")
    (repo / "README.md").write_text("# Synthetic worktree demo\n", encoding="utf-8")
    AgentWorktreeService._git(repo, "add", "README.md")
    AgentWorktreeService._git(repo, "commit", "-m", "initial synthetic fixture")
    request = MissionRequest(
        task_id="demo-task-001",
        idempotency_key="agent-worktrees-demo-v1",
        agent_id="demo-agent",
        owner="demo-platform-team",
        owned_paths=("src/demo", "artifacts/demo-report.json"),
        acceptance_criteria=("demo tests pass", "demo report exists"),
    )
    with SQLiteMissionStore(workspace / "state.sqlite3") as store:
        service = AgentWorktreeService(store)
        mission, created = service.register(
            request,
            repo=repo,
            worktree_root=workspace / "worktrees",
        )
        running = service.provision(mission.mission_id, actor="demo-scheduler")
        worktree = Path(running.worktree_path)
        feature = worktree / "src" / "demo" / "result.txt"
        feature.parent.mkdir(parents=True)
        feature.write_text("isolated worktree verified\n", encoding="utf-8")
        artifact = worktree / "artifacts" / "demo-report.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            json.dumps({"branch": running.branch, "status": "pass"}, sort_keys=True),
            encoding="utf-8",
        )
        service._git(worktree, "add", "src/demo/result.txt", "artifacts/demo-report.json")
        service._git(worktree, "commit", "-m", "complete synthetic worktree mission")
        head = service._git(worktree, "rev-parse", "HEAD").stdout.strip()
        evidence = EvidenceBundle(
            commit_sha=head,
            tests=("synthetic-git-journey:pass",),
            artifacts=("artifacts/demo-report.json",),
            criteria={"demo tests pass": True, "demo report exists": True},
            produced_by="demo-agent",
            notes=("No remote, account, credential, or private repository was used.",),
        )
        service.complete(mission.mission_id, evidence, actor="demo-agent")
        service._git(repo, "merge", "--ff-only", running.branch)
        cleaned = service.cleanup(mission.mission_id, actor="demo-integrator")
        return {
            "created": created,
            "mission": cleaned.to_dict(),
            "events": store.events(mission.mission_id),
            "audit": service.audit(repo=repo),
            "metrics": store.metrics(),
        }


def run(args: argparse.Namespace) -> object:
    if args.command == "demo":
        return _run_demo(Path(args.workspace).resolve())
    with SQLiteMissionStore(args.db) as store:
        service = AgentWorktreeService(store)
        if args.command == "register":
            mission, created = service.register(
                MissionRequest.from_dict(_load_json(args.request)),
                repo=args.repo,
                worktree_root=args.worktree_root,
            )
            return {"created": created, "mission": mission.to_dict()}
        if args.command == "provision":
            return service.provision(args.mission, actor=args.actor).to_dict()
        if args.command == "complete":
            evidence = EvidenceBundle.from_dict(_load_json(args.evidence))
            return service.complete(args.mission, evidence, actor=args.actor).to_dict()
        if args.command == "fail":
            return service.fail(args.mission, actor=args.actor, reason=args.reason).to_dict()
        if args.command == "retry":
            return service.retry(args.mission, actor=args.actor).to_dict()
        if args.command == "wait":
            return service.wait(args.mission, actor=args.actor, reason=args.reason).to_dict()
        if args.command == "resume":
            return service.resume(args.mission, actor=args.actor, reason=args.reason).to_dict()
        if args.command == "intervene":
            return store.record_intervention(
                args.mission,
                actor=args.actor,
                reason=args.reason,
            ).to_dict()
        if args.command == "cleanup":
            return service.cleanup(
                args.mission,
                actor=args.actor,
                keep_branch=args.keep_branch,
            ).to_dict()
        if args.command == "inspect":
            return {
                "mission": store.get(args.mission).to_dict(),
                "events": store.events(args.mission),
            }
        if args.command == "list":
            state = None if args.state is None else MissionState(args.state)
            return [item.to_dict() for item in store.list(state)]
        if args.command == "recover":
            return [item.to_dict() for item in service.recover(actor=args.actor)]
        if args.command == "audit":
            return service.audit(repo=args.repo)
        if args.command == "metrics":
            return store.metrics()
    raise ValueError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        _emit(run(args))
        return 0
    except MissionNotFound as exc:
        _emit({"error": "mission-not-found", "message": str(exc.args[0])}, stream=sys.stderr)
        return 3
    except (GitCommandError, SafetyError, InvalidTransition) as exc:
        _emit({"error": "operation-rejected", "message": str(exc)}, stream=sys.stderr)
        return 4
    except (OSError, ValueError) as exc:
        _emit({"error": "invalid-input", "message": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
