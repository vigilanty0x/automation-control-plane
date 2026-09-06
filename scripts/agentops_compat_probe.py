from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any

from automation_control_plane.agentops.circuits import simulate_circuit
from automation_control_plane.agentops.context import plan_context
from automation_control_plane.agentops.inbox import project_inbox
from automation_control_plane.agentops.quota import simulate_quota
from automation_control_plane.agentops.routing import evaluate_routing
from automation_control_plane.agentops.sessions import record_session

AGENTMESH_SOURCE_SHA = "320f5116f6582519d1609ce87287fd9ff7267eb3"
AGENTMESH_CORE_BLOB_SHA = "fded87d2490071d959845312163368485c7db6df"
CONTEXT_SOURCE_SHA = "35bb3e05d05ad870715b740143c429f08eda25e7"
CONTEXT_MODULE_BLOB_SHA = "9e3d21293ab01231977918ee829d334d129da6a2"
QUOTA_SOURCE_SHA = "e99000cecf12432365e8ccfc8fa6e4b1d18ad15f"
QUOTA_MODULE_BLOB_SHA = "4b7351a57de1b2a38c871f465ab5a133b8315e97"
SESSION_SOURCE_SHA = "2363c4efe0c61158c523a6dfc3d29cb3d7af1c54"
SESSION_MODULE_BLOB_SHA = "13df1c46eee5316ea53588538e6e7c785caf75d9"
CIRCUIT_SOURCE_SHA = "2924dfb6eed8a208788491fa1d50fa6bd99e4359"
CIRCUIT_CORE_BLOB_SHA = "f5986b1240f663a91c8581fed561b740fcf67d63"
INBOX_SOURCE_SHA = "748f237659f98a2a49478aa58913e71e59a03433"
INBOX_CONTRACT_BLOB_SHA = "35ce0febe2d31bf775256145de3967cd7d0cc3a1"
INBOX_CORE_BLOB_SHA = "709e97c60f0c8e71443195a18da603389854a7cb"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return sha1(header + data).hexdigest()


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _route_payload(*, healthy: bool) -> dict[str, Any]:
    return {
        "agents": [
            {"id": "planner", "healthy": True, "owner": "planning", "capabilities": []},
            {"id": "worker", "healthy": healthy, "owner": "runtime", "capabilities": ["execute"]},
        ],
        "routes": [
            {"source": "planner", "target": "worker", "capability": "execute", "owner": "runtime"}
        ],
    }


def _adapt_context(source_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_tokens": source_input["window_tokens"],
        "reserve_output_tokens": source_input["output_reserve"],
        "sections": [
            {
                "id": section["name"],
                "tokens": section["tokens"],
                "required": section["required"],
                "priority": section.get("priority", 0),
            }
            for section in source_input["sections"]
        ],
    }


def _adapt_quota(source_input: dict[str, Any]) -> dict[str, Any]:
    budget = source_input["budget"]
    return {
        "budgets": {
            "tokens": budget["tokens"],
            "time_ms": budget["seconds"] * 1000,
            "micro_cost": budget["cost_micros"],
        },
        "tasks": [
            {
                "id": task["id"],
                "priority": task.get("priority", 0),
                "required": False,
                "tokens": task["tokens"],
                "time_ms": task["seconds"] * 1000,
                "micro_cost": task["cost_micros"],
            }
            for task in source_input["tasks"]
        ],
    }


def _adapt_session(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "session_id": "compat-session",
        "events": [
            {
                "event_id": f"event-{event['sequence']}",
                "actor": "source-agent",
                "type": event["kind"],
                "at": f"2026-08-18T00:00:{event['sequence']:02d}Z",
                "data": event["content"],
            }
            for event in events
        ],
    }


def _source_context_selection(result: dict[str, Any]) -> tuple[str, ...]:
    return tuple(result.get("selected", ()))


def _target_context_selection(result: dict[str, Any]) -> tuple[str, ...]:
    details = result.get("details", {})
    return tuple(details.get("included", ())) if isinstance(details, dict) else ()


def _target_quota_admitted(result: dict[str, Any]) -> tuple[str, ...]:
    details = result.get("details", {})
    return tuple(details.get("admitted", ())) if isinstance(details, dict) else ()


def _load_agent_inbox(source_root: Path):
    source_path = str(source_root / "src")
    previous_path = list(sys.path)
    old_modules = {key: value for key, value in sys.modules.items() if key == "agent_inbox" or key.startswith("agent_inbox.")}
    for key in list(old_modules):
        sys.modules.pop(key, None)
    sys.path.insert(0, source_path)
    try:
        import agent_inbox  # type: ignore
        return agent_inbox, previous_path, old_modules
    except Exception:
        sys.path[:] = previous_path
        sys.modules.update(old_modules)
        raise


def _restore_agent_inbox(previous_path: list[str], old_modules: dict[str, ModuleType]) -> None:
    for key in [key for key in sys.modules if key == "agent_inbox" or key.startswith("agent_inbox.")]:
        sys.modules.pop(key, None)
    sys.modules.update(old_modules)
    sys.path[:] = previous_path


def run(
    agentmesh_root: Path,
    context_root: Path,
    quota_root: Path,
    session_root: Path,
    circuit_root: Path,
    inbox_root: Path,
) -> dict[str, Any]:
    source_files = {
        "agentmesh": (agentmesh_root / "src" / "agentmesh" / "core.py", AGENTMESH_CORE_BLOB_SHA),
        "context-window-budgeter": (context_root / "src" / "context_window_budgeter" / "__init__.py", CONTEXT_MODULE_BLOB_SHA),
        "agent-quota-simulator": (quota_root / "src" / "agent_quota_simulator" / "__init__.py", QUOTA_MODULE_BLOB_SHA),
        "agent-session-recorder": (session_root / "src" / "agent_session_recorder" / "__init__.py", SESSION_MODULE_BLOB_SHA),
        "circuit-breaker-lab": (circuit_root / "src" / "circuit_breaker_lab" / "core.py", CIRCUIT_CORE_BLOB_SHA),
        "agent-inbox-contract": (inbox_root / "src" / "agent_inbox" / "contract.py", INBOX_CONTRACT_BLOB_SHA),
        "agent-inbox-core": (inbox_root / "src" / "agent_inbox" / "inbox.py", INBOX_CORE_BLOB_SHA),
    }
    observed_blobs = {name: _git_blob_sha(path) for name, (path, _) in source_files.items()}
    blob_match = {name: observed_blobs[name] == expected for name, (_, expected) in source_files.items()}
    if not all(blob_match.values()):
        result = {
            "status": "blocked",
            "kind": "agentops_compatibility_probe",
            "source_blob_match": blob_match,
            "observed_blobs": observed_blobs,
            "reason": "one or more source checkouts do not match the reviewed source blobs",
            "migration_performed": False,
            "legacy_aliases_activated": False,
        }
        result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
        return result

    agentmesh = _load_module(source_files["agentmesh"][0], "agentops_probe_agentmesh")
    context = _load_module(source_files["context-window-budgeter"][0], "agentops_probe_context")
    quota = _load_module(source_files["agent-quota-simulator"][0], "agentops_probe_quota")
    session = _load_module(source_files["agent-session-recorder"][0], "agentops_probe_session")
    circuit = _load_module(source_files["circuit-breaker-lab"][0], "agentops_probe_circuit")

    mesh_source_positive = agentmesh.evaluate({"agent_count": 2, "healthy_agents": 2, "route_count": 1})
    mesh_source_negative = agentmesh.evaluate({"agent_count": 2, "healthy_agents": 1, "route_count": 1})
    mesh_target_positive = evaluate_routing(_route_payload(healthy=True))
    mesh_target_negative = evaluate_routing(_route_payload(healthy=False))
    mesh_invariant_match = (
        mesh_source_positive["status"] == mesh_target_positive["status"] == "passed"
        and mesh_source_negative["status"] == mesh_target_negative["status"] == "failed"
    )

    context_basic_input = {
        "window_tokens": 10,
        "output_reserve": 2,
        "sections": [
            {"name": "system", "tokens": 2, "required": True},
            {"name": "memory", "tokens": 3, "required": False, "priority": 5},
        ],
    }
    context_overflow_input = {
        "window_tokens": 4,
        "output_reserve": 2,
        "sections": [{"name": "system", "tokens": 3, "required": True}],
    }
    context_tiebreak_input = {
        "window_tokens": 3,
        "output_reserve": 0,
        "sections": [
            {"name": "a", "tokens": 3, "required": False, "priority": 10},
            {"name": "b", "tokens": 1, "required": False, "priority": 10},
        ],
    }
    source_basic = context.budget(context_basic_input)
    target_basic = plan_context(_adapt_context(context_basic_input))
    source_overflow = context.budget(context_overflow_input)
    target_overflow = plan_context(_adapt_context(context_overflow_input))
    source_tiebreak = context.budget(context_tiebreak_input)
    target_tiebreak = plan_context(_adapt_context(context_tiebreak_input))
    context_basic_match = (
        source_basic.get("ok") is True
        and target_basic.get("status") == "passed"
        and _source_context_selection(source_basic) == _target_context_selection(target_basic)
    )
    context_overflow_match = source_overflow.get("ok") is False and target_overflow.get("status") == "failed"
    source_tiebreak_selection = _source_context_selection(source_tiebreak)
    target_tiebreak_selection = _target_context_selection(target_tiebreak)
    context_tiebreak_mismatch = source_tiebreak_selection != target_tiebreak_selection

    quota_input = {
        "budget": {"tokens": 8, "seconds": 4, "cost_micros": 8},
        "tasks": [
            {"id": "a", "tokens": 4, "seconds": 1, "cost_micros": 3, "priority": 10},
            {"id": "b", "tokens": 4, "seconds": 3, "cost_micros": 5, "priority": 5},
            {"id": "c", "tokens": 1, "seconds": 1, "cost_micros": 1, "priority": 1},
        ],
    }
    quota_source = quota.simulate(quota_input)
    quota_target = simulate_quota(_adapt_quota(quota_input))
    quota_selection_match = (
        quota_source.get("ok") is True
        and quota_target.get("status") == "passed"
        and tuple(quota_source.get("admitted", ())) == _target_quota_admitted(quota_target)
    )
    quota_source_negative = quota.simulate({"budget": {"tokens": -1, "seconds": 1, "cost_micros": 1}, "tasks": []})
    quota_target_negative = simulate_quota({"budgets": {"tokens": -1, "time_ms": 1000, "micro_cost": 1}, "tasks": []})
    quota_invalid_budget_match = quota_source_negative.get("ok") is False and quota_target_negative.get("status") == "blocked"

    session_events = [{"sequence": 1, "kind": "input", "content": {"message": "synthetic"}}]
    session_source = session.record(session_events)
    session_target = record_session(_adapt_session(session_events))
    session_positive_match = session_source.get("ok") is True and session_target.get("status") == "passed"
    sensitive_events = [{"sequence": 1, "kind": "input", "content": {"token": "synthetic-not-a-secret"}}]
    session_source_sensitive = session.record(sensitive_events)
    session_target_sensitive = record_session(_adapt_session(sensitive_events))
    session_sensitive_mismatch = session_source_sensitive.get("ok") is True and session_target_sensitive.get("status") == "blocked"
    session_bad_sequence = session.record([{"sequence": 2, "kind": "input", "content": "bad"}])
    session_sequence_counterproof = session_bad_sequence.get("ok") is False

    circuit_source_basic = circuit.simulate(
        [
            {"at_ms": 0, "success": False},
            {"at_ms": 1, "success": False},
            {"at_ms": 2, "success": False},
        ],
        threshold=3,
        cooldown_ms=1000,
    )
    circuit_target_basic = simulate_circuit(
        {
            "policy": {"failure_threshold": 3, "success_threshold": 1, "max_events": 4},
            "initial_state": "closed",
            "events": [
                {"id": "f1", "outcome": "failure"},
                {"id": "f2", "outcome": "failure"},
                {"id": "f3", "outcome": "failure"},
            ],
        }
    )
    circuit_open_match = circuit_source_basic.get("state") == circuit_target_basic.get("details", {}).get("final_state") == "open"
    circuit_source_open_event = circuit.simulate(
        [
            {"at_ms": 0, "success": False},
            {"at_ms": 1, "success": False},
            {"at_ms": 2, "success": False},
            {"at_ms": 100, "success": False},
        ],
        threshold=3,
        cooldown_ms=1000,
    )
    circuit_target_open_event = simulate_circuit(
        {
            "policy": {"failure_threshold": 3, "success_threshold": 1, "max_events": 4},
            "initial_state": "closed",
            "events": [
                {"id": "f1", "outcome": "failure"},
                {"id": "f2", "outcome": "failure"},
                {"id": "f3", "outcome": "failure"},
                {"id": "f4", "outcome": "failure"},
            ],
        }
    )
    circuit_open_event_mismatch = (
        circuit_source_open_event["events"][-1]["allowed"] is False
        and circuit_source_open_event["state"] == "open"
        and circuit_target_open_event.get("status") == "failed"
    )

    agent_inbox, previous_path, old_modules = _load_agent_inbox(inbox_root)
    try:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "source-inbox.sqlite"
            source_inbox = agent_inbox.AgentInbox(database)
            source_mission = source_inbox.enqueue(
                agent_inbox.MissionSpec(
                    idempotency_key="compat-mission",
                    title="Synthetic compatibility mission",
                    payload={"synthetic": True},
                    priority=50,
                )
            )
            source_mutated = database.is_file() and database.stat().st_size > 0
    finally:
        _restore_agent_inbox(previous_path, old_modules)
    target_inbox = project_inbox(
        {
            "now_epoch_ms": 0,
            "jobs": [
                {
                    "job_id": "compat-mission",
                    "workflow_id": "synthetic-workflow",
                    "state": "queued",
                    "priority": 50,
                    "deadline_epoch_ms": None,
                    "approval_required": False,
                    "last_error": None,
                }
            ],
        }
    )
    inbox_state_match = source_mission.get("status") == "queued" and target_inbox.get("details", {}).get("items", [{}])[0].get("state") == "queued"
    inbox_mutation_mismatch = source_mutated and target_inbox.get("details", {}).get("mutation_performed") is False

    expected_counterproofs = all(
        (
            mesh_invariant_match,
            context_basic_match,
            context_overflow_match,
            context_tiebreak_mismatch,
            quota_selection_match,
            quota_invalid_budget_match,
            session_positive_match,
            session_sensitive_mismatch,
            session_sequence_counterproof,
            circuit_open_match,
            circuit_open_event_mismatch,
            inbox_state_match,
            inbox_mutation_mismatch,
        )
    )
    result = {
        "status": "passed" if expected_counterproofs else "failed",
        "kind": "agentops_compatibility_probe",
        "sources": {
            "agentmesh": {
                "source_sha": AGENTMESH_SOURCE_SHA,
                "source_blob_sha": AGENTMESH_CORE_BLOB_SHA,
                "semantic_invariant_match": mesh_invariant_match,
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "count-only source schema requires an explicit adapter to the target identity/ownership route schema",
            },
            "context-window-budgeter": {
                "source_sha": CONTEXT_SOURCE_SHA,
                "source_blob_sha": CONTEXT_MODULE_BLOB_SHA,
                "basic_selection_match": context_basic_match,
                "required_overflow_match": context_overflow_match,
                "tie_break_mismatch_detected": context_tiebreak_mismatch,
                "source_tie_break_selection": list(source_tiebreak_selection),
                "target_tie_break_selection": list(target_tiebreak_selection),
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "equal-priority optional sections use different tie-break rules; an alias would change behavior",
            },
            "agent-quota-simulator": {
                "source_sha": QUOTA_SOURCE_SHA,
                "source_blob_sha": QUOTA_MODULE_BLOB_SHA,
                "adapted_selection_match": quota_selection_match,
                "invalid_budget_counterproof_match": quota_invalid_budget_match,
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "source uses budget/seconds/cost_micros and has no required-task concept; target uses budgets/time_ms/micro_cost plus required-task semantics",
            },
            "agent-session-recorder": {
                "source_sha": SESSION_SOURCE_SHA,
                "source_blob_sha": SESSION_MODULE_BLOB_SHA,
                "adapted_positive_match": session_positive_match,
                "sequence_counterproof_present": session_sequence_counterproof,
                "sensitive_key_semantic_mismatch_detected": session_sensitive_mismatch,
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "target rejects sensitive key names and uses richer timestamp/actor/session hash-chain records; direct legacy alias would change accepted inputs and receipts",
            },
            "circuit-breaker-lab": {
                "source_sha": CIRCUIT_SOURCE_SHA,
                "source_blob_sha": CIRCUIT_CORE_BLOB_SHA,
                "threshold_open_invariant_match": circuit_open_match,
                "open_state_event_mismatch_detected": circuit_open_event_mismatch,
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "source silently denies calls while open until elapsed wall time; target requires an explicit cooldown_elapsed event and fail-closes invalid open-state events",
            },
            "agent-inbox": {
                "source_sha": INBOX_SOURCE_SHA,
                "source_contract_blob_sha": INBOX_CONTRACT_BLOB_SHA,
                "source_core_blob_sha": INBOX_CORE_BLOB_SHA,
                "queued_state_projection_match": inbox_state_match,
                "source_durable_mutation_observed": source_mutated,
                "target_read_only_projection_observed": target_inbox.get("details", {}).get("mutation_performed") is False,
                "durability_semantic_mismatch_detected": inbox_mutation_mismatch,
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "source is a durable mutating SQLite mission queue; prepared agentops inbox is intentionally a read-only projection while durable queue behavior remains owned by the control-plane core",
            },
        },
        "source_blob_match": blob_match,
        "migration_performed": False,
        "legacy_aliases_activated": False,
        "expected_counterproofs_present": expected_counterproofs,
    }
    result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute SHA-bound AgentOps source/target compatibility counter-proofs.")
    parser.add_argument("--agentmesh-root", type=Path, required=True)
    parser.add_argument("--context-root", type=Path, required=True)
    parser.add_argument("--quota-root", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--circuit-root", type=Path, required=True)
    parser.add_argument("--inbox-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run(
        args.agentmesh_root,
        args.context_root,
        args.quota_root,
        args.session_root,
        args.circuit_root,
        args.inbox_root,
    )
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
