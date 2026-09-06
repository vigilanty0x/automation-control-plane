from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from automation_control_plane.agentops.adapters import rehearse_adapter

SOURCE_FILES = {
    "agentmesh": (
        "320f5116f6582519d1609ce87287fd9ff7267eb3",
        "fded87d2490071d959845312163368485c7db6df",
        "src/agentmesh/core.py",
    ),
    "context-window-budgeter": (
        "35bb3e05d05ad870715b740143c429f08eda25e7",
        "9e3d21293ab01231977918ee829d334d129da6a2",
        "src/context_window_budgeter/__init__.py",
    ),
    "agent-quota-simulator": (
        "e99000cecf12432365e8ccfc8fa6e4b1d18ad15f",
        "4b7351a57de1b2a38c871f465ab5a133b8315e97",
        "src/agent_quota_simulator/__init__.py",
    ),
    "agent-session-recorder": (
        "2363c4efe0c61158c523a6dfc3d29cb3d7af1c54",
        "13df1c46eee5316ea53588538e6e7c785caf75d9",
        "src/agent_session_recorder/__init__.py",
    ),
    "circuit-breaker-lab": (
        "2924dfb6eed8a208788491fa1d50fa6bd99e4359",
        "f5986b1240f663a91c8581fed561b740fcf67d63",
        "src/circuit_breaker_lab/core.py",
    ),
}
TARGET_ADAPTER_BLOB_SHA = "c0d72bdcbfde735c07f7c3fb7709536b12bb9bbe"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load exact source module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def run(
    *,
    agentmesh_root: Path,
    context_root: Path,
    quota_root: Path,
    session_root: Path,
    circuit_root: Path,
    target_root: Path,
) -> dict[str, Any]:
    roots = {
        "agentmesh": agentmesh_root,
        "context-window-budgeter": context_root,
        "agent-quota-simulator": quota_root,
        "agent-session-recorder": session_root,
        "circuit-breaker-lab": circuit_root,
    }
    paths = {
        repository: roots[repository] / relative
        for repository, (_, _, relative) in SOURCE_FILES.items()
    }
    observed_source_blobs = {repository: _git_blob_sha(path) for repository, path in paths.items()}
    source_blob_match = {
        repository: observed_source_blobs[repository] == SOURCE_FILES[repository][1]
        for repository in SOURCE_FILES
    }
    adapter_path = target_root / "src" / "automation_control_plane" / "agentops" / "adapters.py"
    observed_adapter_blob = _git_blob_sha(adapter_path)
    target_blob_match = observed_adapter_blob == TARGET_ADAPTER_BLOB_SHA
    if not all(source_blob_match.values()) or not target_blob_match:
        result = {
            "kind": "agentops_adapter_compatibility_probe",
            "status": "blocked",
            "source_blob_match": source_blob_match,
            "target_adapter_blob_match": target_blob_match,
            "observed_source_blobs": observed_source_blobs,
            "observed_target_adapter_blob": observed_adapter_blob,
            "migration_performed": False,
            "legacy_aliases_activated": False,
        }
        result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
        return result

    source = {
        repository: _load_module(path, "agentops_adapter_probe_" + repository.replace("-", "_"))
        for repository, path in paths.items()
    }

    mesh_payload = {"agent_count": 2, "healthy_agents": 2, "route_count": 1}
    mesh_source = source["agentmesh"].evaluate(mesh_payload)
    mesh_adapter = rehearse_adapter(
        {
            "source_repository": "agentmesh",
            "source_sha": SOURCE_FILES["agentmesh"][0],
            "source_payload": mesh_payload,
            "adapter_input": {
                "agents": [
                    {"id": "a", "healthy": True, "owner": "team-a", "capabilities": ["chat"]},
                    {"id": "b", "healthy": True, "owner": "team-b", "capabilities": ["code"]},
                ],
                "routes": [{"source": "a", "target": "b", "capability": "code", "owner": "team-b"}],
                "required_capabilities": ["code"],
            },
        }
    )
    mesh_match = (
        mesh_source["status"] == "passed"
        and mesh_adapter["status"] == "passed"
        and mesh_adapter["details"]["count_match"] is True
    )

    context_payload = {
        "window_tokens": 8,
        "output_reserve": 2,
        "sections": [
            {"name": "a", "tokens": 4, "required": False, "priority": 10},
            {"name": "b", "tokens": 2, "required": False, "priority": 10},
            {"name": "system", "tokens": 2, "required": True},
        ],
    }
    context_source = source["context-window-budgeter"].budget(context_payload)
    context_adapter = rehearse_adapter(
        {
            "source_repository": "context-window-budgeter",
            "source_sha": SOURCE_FILES["context-window-budgeter"][0],
            "source_payload": context_payload,
        }
    )
    context_match = (
        context_source["ok"] is True
        and context_adapter["status"] == "passed"
        and context_source["selected"] == context_adapter["details"]["source_plan"]["selected"]
        and context_source["dropped"] == context_adapter["details"]["source_plan"]["dropped"]
        and context_adapter["details"]["selection_match"] is True
    )

    quota_payload = {
        "budget": {"tokens": 10, "seconds": 5, "cost_micros": 20},
        "tasks": [
            {"id": "a", "tokens": 4, "seconds": 3, "cost_micros": 10, "priority": 10},
            {"id": "b", "tokens": 7, "seconds": 2, "cost_micros": 5, "priority": 5},
        ],
    }
    quota_source = source["agent-quota-simulator"].simulate(quota_payload)
    quota_adapter = rehearse_adapter(
        {
            "source_repository": "agent-quota-simulator",
            "source_sha": SOURCE_FILES["agent-quota-simulator"][0],
            "source_payload": quota_payload,
        }
    )
    quota_match = (
        quota_source["ok"] is True
        and quota_adapter["status"] == "passed"
        and quota_source["admitted"] == quota_adapter["details"]["source_admitted"]
        and [item["id"] for item in quota_source["rejected"]] == quota_adapter["details"]["source_rejected"]
        and quota_adapter["details"]["selection_match"] is True
        and quota_adapter["details"]["remaining_match"] is True
    )

    session_events = [
        {"sequence": 1, "kind": "input", "content": {"text": "hello"}},
        {"sequence": 2, "kind": "output", "content": {"text": "world"}},
    ]
    session_source = source["agent-session-recorder"].record(session_events)
    session_adapter = rehearse_adapter(
        {
            "source_repository": "agent-session-recorder",
            "source_sha": SOURCE_FILES["agent-session-recorder"][0],
            "source_payload": {"events": session_events},
            "adapter_input": {
                "session_id": "legacy-session",
                "actor": "legacy-agent",
                "timestamps": ["2026-08-18T12:00:00Z", "2026-08-18T12:00:01Z"],
            },
        }
    )
    sensitive_source = source["agent-session-recorder"].record(
        [{"sequence": 1, "kind": "input", "content": {"token": "synthetic"}}]
    )
    sensitive_adapter = rehearse_adapter(
        {
            "source_repository": "agent-session-recorder",
            "source_sha": SOURCE_FILES["agent-session-recorder"][0],
            "source_payload": {"events": [{"sequence": 1, "kind": "input", "content": {"token": "synthetic"}}]},
            "adapter_input": {
                "session_id": "legacy-session",
                "actor": "legacy-agent",
                "timestamps": ["2026-08-18T12:00:00Z"],
            },
        }
    )
    session_match = (
        session_source["ok"] is True
        and session_adapter["status"] == "passed"
        and session_adapter["details"]["source_event_count"] == session_source["count"]
        and session_adapter["details"]["authenticity_transferred"] is False
    )
    sensitive_narrowing_proven = sensitive_source["ok"] is True and sensitive_adapter["status"] == "failed"

    circuit_payload = {
        "threshold": 2,
        "cooldown_ms": 1000,
        "events": [
            {"at_ms": 0, "success": False},
            {"at_ms": 10, "success": False},
            {"at_ms": 500, "success": True},
            {"at_ms": 1010, "success": True},
        ],
    }
    circuit_source = source["circuit-breaker-lab"].simulate(**circuit_payload)
    circuit_adapter = rehearse_adapter(
        {
            "source_repository": "circuit-breaker-lab",
            "source_sha": SOURCE_FILES["circuit-breaker-lab"][0],
            "source_payload": circuit_payload,
        }
    )
    source_trace = [
        {"allowed": item["allowed"], "state": item["state"]}
        for item in circuit_source["events"]
    ]
    adapter_trace = [
        {"allowed": item["allowed"], "state": item["state"]}
        for item in circuit_adapter.get("details", {}).get("source_trace", [])
    ]
    circuit_match = (
        circuit_adapter["status"] == "passed"
        and circuit_source["state"] == circuit_adapter["details"]["source_final_state"]
        and source_trace == adapter_trace
        and circuit_adapter["details"]["final_state_match"] is True
    )

    checks = {
        "agentmesh_exact_source_fixture_match": mesh_match,
        "context_exact_source_tiebreak_match": context_match,
        "quota_exact_source_fixture_match": quota_match,
        "session_exact_source_fixture_match": session_match,
        "session_sensitive_key_narrowing_proven": sensitive_narrowing_proven,
        "circuit_exact_source_trace_match": circuit_match,
    }
    result = {
        "kind": "agentops_adapter_compatibility_probe",
        "status": "passed" if all(checks.values()) else "failed",
        "source_shas": {repository: values[0] for repository, values in SOURCE_FILES.items()},
        "source_blob_match": source_blob_match,
        "target_adapter_blob_match": target_blob_match,
        "checks": checks,
        "migration_performed": False,
        "legacy_aliases_activated": False,
        "consumer_mutation_performed": False,
        "source_retirement_authorized": False,
    }
    result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SHA-bound proof for the five reversible AgentOps candidate adapters.")
    parser.add_argument("--agentmesh-root", required=True)
    parser.add_argument("--context-root", required=True)
    parser.add_argument("--quota-root", required=True)
    parser.add_argument("--session-root", required=True)
    parser.add_argument("--circuit-root", required=True)
    parser.add_argument("--target-root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run(
        agentmesh_root=Path(args.agentmesh_root),
        context_root=Path(args.context_root),
        quota_root=Path(args.quota_root),
        session_root=Path(args.session_root),
        circuit_root=Path(args.circuit_root),
        target_root=Path(args.target_root),
    )
    Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(_canonical(result))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
