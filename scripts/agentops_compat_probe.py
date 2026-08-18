from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from automation_control_plane.agentops.context import plan_context
from automation_control_plane.agentops.routing import evaluate_routing

AGENTMESH_SOURCE_SHA = "320f5116f6582519d1609ce87287fd9ff7267eb3"
AGENTMESH_CORE_BLOB_SHA = "fded87d2490071d959845312163368485c7db6df"
CONTEXT_SOURCE_SHA = "35bb3e05d05ad870715b740143c429f08eda25e7"
CONTEXT_MODULE_BLOB_SHA = "9e3d21293ab01231977918ee829d334d129da6a2"


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
    sections = []
    for section in source_input["sections"]:
        sections.append(
            {
                "id": section["name"],
                "tokens": section["tokens"],
                "required": section["required"],
                "priority": section.get("priority", 0),
            }
        )
    return {
        "window_tokens": source_input["window_tokens"],
        "reserve_output_tokens": source_input["output_reserve"],
        "sections": sections,
    }


def _source_context_selection(result: dict[str, Any]) -> tuple[str, ...]:
    return tuple(result.get("selected", ()))


def _target_context_selection(result: dict[str, Any]) -> tuple[str, ...]:
    details = result.get("details", {})
    return tuple(details.get("included", ())) if isinstance(details, dict) else ()


def run(agentmesh_root: Path, context_root: Path) -> dict[str, Any]:
    agentmesh_core = agentmesh_root / "src" / "agentmesh" / "core.py"
    context_module = context_root / "src" / "context_window_budgeter" / "__init__.py"
    observed_blobs = {
        "agentmesh": _git_blob_sha(agentmesh_core),
        "context-window-budgeter": _git_blob_sha(context_module),
    }
    blob_match = {
        "agentmesh": observed_blobs["agentmesh"] == AGENTMESH_CORE_BLOB_SHA,
        "context-window-budgeter": observed_blobs["context-window-budgeter"] == CONTEXT_MODULE_BLOB_SHA,
    }
    if not all(blob_match.values()):
        result = {
            "status": "blocked",
            "kind": "agentops_compatibility_probe",
            "source_blob_match": blob_match,
            "observed_blobs": observed_blobs,
            "reason": "source checkout does not match the reviewed source blob",
            "migration_performed": False,
            "legacy_aliases_activated": False,
        }
        result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
        return result

    agentmesh = _load_module(agentmesh_core, "agentops_probe_agentmesh")
    context = _load_module(context_module, "agentops_probe_context")

    mesh_positive_input = {"agent_count": 2, "healthy_agents": 2, "route_count": 1}
    mesh_negative_input = {"agent_count": 2, "healthy_agents": 1, "route_count": 1}
    mesh_source_positive = agentmesh.evaluate(mesh_positive_input)
    mesh_source_negative = agentmesh.evaluate(mesh_negative_input)
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
    context_tiebreak_mismatch_detected = source_tiebreak_selection != target_tiebreak_selection

    expected_counterproofs = mesh_invariant_match and context_basic_match and context_overflow_match and context_tiebreak_mismatch_detected
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
                "positive_status": {"source": mesh_source_positive["status"], "target": mesh_target_positive["status"]},
                "negative_status": {"source": mesh_source_negative["status"], "target": mesh_target_negative["status"]},
                "reason": "count-only source schema requires an explicit adapter to the target identity/ownership route schema",
            },
            "context-window-budgeter": {
                "source_sha": CONTEXT_SOURCE_SHA,
                "source_blob_sha": CONTEXT_MODULE_BLOB_SHA,
                "basic_selection_match": context_basic_match,
                "required_overflow_match": context_overflow_match,
                "tie_break_mismatch_detected": context_tiebreak_mismatch_detected,
                "source_tie_break_selection": list(source_tiebreak_selection),
                "target_tie_break_selection": list(target_tiebreak_selection),
                "cli_compatible": False,
                "migration_gate": "blocked",
                "reason": "equal-priority optional sections use different tie-break rules; an alias would change behavior",
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run(args.agentmesh_root, args.context_root)
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
