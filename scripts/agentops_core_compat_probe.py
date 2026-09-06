from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha1, sha256
import importlib.util
import json
from pathlib import Path
import tempfile
from types import ModuleType
from typing import Any

from automation_control_plane import ConflictError, ControlPlane, ControlPlaneStore, WorkflowDefinition
from automation_control_plane.core import transition
from automation_control_plane.models import ModelError, RetryPolicy, StepDefinition

BUDGETER_SOURCE_SHA = "cfa9c0a8830f3e2e3a11602da590e347f8483d2f"
BUDGETER_MODELS_BLOB_SHA = "a68f5994f05a5d8a5205d3018a2dbfb6bceb0d27"
RETRY_SOURCE_SHA = "38f97aaa6796fda6956202aad9086e3e5e8ada9f"
RETRY_CORE_BLOB_SHA = "ddf4ac4afaa8a1df94707553c1b647a57d2138bb"
HITL_SOURCE_SHA = "db281bf7ff971a2fd4ca6af9e495b2c0fd6cd30b"
HITL_CORE_BLOB_SHA = "49afba166f0e4b450dc2fc0171394c8ab801ec5d"
IDEMPOTENCY_SOURCE_SHA = "80e78a4d9b73aeabb200578c2ecb5090f31a410f"
IDEMPOTENCY_CORE_BLOB_SHA = "2d14618dff4305998a6b965d4bd9854f80b68985"
TASKGRAPH_SOURCE_SHA = "937abf0d096cb1f6ff48aa09b8dd8a69d9a36c0d"
TASKGRAPH_MODELS_BLOB_SHA = "b330dbb3f4f89eb52a8dfffe95fc5c24c7b3c573"
TIMEOUT_SOURCE_SHA = "a2a053e39a6eaa40cde7d81f87ee4838cf562583"
TIMEOUT_CORE_BLOB_SHA = "b1191737e8bdaee427d66eaf7730a38da78102c8"

TARGET_CORE_BLOB_SHA = "cd3f2ce6dcf3a7ef675805838f064e3338554a7a"
TARGET_ENGINE_BLOB_SHA = "ffa49b37cd148ae69272ea919fd77def7026fd19"
TARGET_MODELS_BLOB_SHA = "5dac44d628168cae85b886641a469322bb11b0e5"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(step_id: str, *, timeout_seconds: Any = 5) -> dict[str, Any]:
    return {
        "id": step_id,
        "handler": "echo",
        "depends_on": [],
        "input": {},
        "required_capability": "handler:echo",
        "approval": "none",
        "estimated_cost": 1,
        "timeout_seconds": timeout_seconds,
        "retry": {
            "max_attempts": 3,
            "initial_delay_seconds": 1,
            "multiplier": 2,
            "max_delay_seconds": 10,
        },
    }


def _workflow(*, budget_units: Any = 10, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workflow_id": "compat-workflow",
        "version": 1,
        "description": "synthetic compatibility workflow",
        "budget_units": budget_units,
        "default_deadline_seconds": 60,
        "triggers": [{"type": "manual"}],
        "steps": steps or [_step("a")],
    }


def _target_idempotency_conflict() -> bool:
    with tempfile.TemporaryDirectory() as directory:
        store = ControlPlaneStore(str(Path(directory) / "control.db"))
        store.initialize()
        control = ControlPlane(store, clock=lambda: datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc))
        definition = WorkflowDefinition.from_dict(_workflow())
        control.register_workflow(definition, principal="admin")
        control.submit(
            "compat-workflow",
            principal="admin",
            trigger={"type": "manual"},
            idempotency_key="same-key",
            payload={"value": 1},
            dry_run=True,
        )
        try:
            control.submit(
                "compat-workflow",
                principal="admin",
                trigger={"type": "manual"},
                idempotency_key="same-key",
                payload={"value": 2},
                dry_run=True,
            )
        except ConflictError:
            return True
        return False


def run(
    budgeter_root: Path,
    retry_root: Path,
    hitl_root: Path,
    idempotency_root: Path,
    taskgraph_root: Path,
    timeout_root: Path,
    target_root: Path,
) -> dict[str, Any]:
    source_files = {
        "agent-budgeter": (budgeter_root / "src" / "agent_budgeter" / "models.py", BUDGETER_MODELS_BLOB_SHA),
        "agent-retry-kit": (retry_root / "src" / "agent_retry_kit" / "core.py", RETRY_CORE_BLOB_SHA),
        "human-in-the-loop-queue": (hitl_root / "src" / "human_in_the_loop_queue" / "core.py", HITL_CORE_BLOB_SHA),
        "idempotency-kit": (idempotency_root / "src" / "idempotency_kit" / "core.py", IDEMPOTENCY_CORE_BLOB_SHA),
        "taskgraph": (taskgraph_root / "src" / "taskgraph" / "models.py", TASKGRAPH_MODELS_BLOB_SHA),
        "timeout-toolkit": (timeout_root / "src" / "timeout_toolkit" / "core.py", TIMEOUT_CORE_BLOB_SHA),
    }
    target_files = {
        "core": (target_root / "src" / "automation_control_plane" / "core.py", TARGET_CORE_BLOB_SHA),
        "engine": (target_root / "src" / "automation_control_plane" / "engine.py", TARGET_ENGINE_BLOB_SHA),
        "models": (target_root / "src" / "automation_control_plane" / "models.py", TARGET_MODELS_BLOB_SHA),
    }
    observed_source_blobs = {name: _git_blob_sha(path) for name, (path, _) in source_files.items()}
    observed_target_blobs = {name: _git_blob_sha(path) for name, (path, _) in target_files.items()}
    source_blob_match = {name: observed_source_blobs[name] == expected for name, (_, expected) in source_files.items()}
    target_blob_match = {name: observed_target_blobs[name] == expected for name, (_, expected) in target_files.items()}
    if not all(source_blob_match.values()) or not all(target_blob_match.values()):
        result = {
            "status": "blocked",
            "kind": "agentops_core_compatibility_probe",
            "source_blob_match": source_blob_match,
            "target_blob_match": target_blob_match,
            "observed_source_blobs": observed_source_blobs,
            "observed_target_blobs": observed_target_blobs,
            "reason": "source or target checkout does not match the reviewed blob set",
            "migration_performed": False,
            "legacy_aliases_activated": False,
        }
        result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
        return result

    budgeter = _load_module(source_files["agent-budgeter"][0], "agentops_core_probe_budgeter")
    retry = _load_module(source_files["agent-retry-kit"][0], "agentops_core_probe_retry")
    hitl = _load_module(source_files["human-in-the-loop-queue"][0], "agentops_core_probe_hitl")
    idempotency = _load_module(source_files["idempotency-kit"][0], "agentops_core_probe_idempotency")
    taskgraph = _load_module(source_files["taskgraph"][0], "agentops_core_probe_taskgraph")
    timeout = _load_module(source_files["timeout-toolkit"][0], "agentops_core_probe_timeout")

    # agent-budgeter: bool is accepted as an int by the source BudgetVector, while
    # the durable workflow model intentionally rejects booleans as integer units.
    source_bool_budget_accepted = False
    try:
        budgeter.BudgetVector(True, 0, 0)
        source_bool_budget_accepted = True
    except Exception:
        pass
    target_bool_budget_blocked = False
    try:
        WorkflowDefinition.from_dict(_workflow(budget_units=True))
    except ModelError:
        target_bool_budget_blocked = True
    budget_dimension_mismatch = source_bool_budget_accepted and target_bool_budget_blocked

    # agent-retry-kit: the source encodes an error taxonomy and millisecond backoff.
    # Durable RetryPolicy uses integer seconds and deliberately does not classify errors.
    source_retryable = retry.decide(0, "timeout", max_attempts=3, base_ms=100, cap_ms=5000)
    source_permanent = retry.decide(0, "permanent", max_attempts=3, base_ms=100, cap_ms=5000)
    target_retry = RetryPolicy.from_dict(
        {"max_attempts": 3, "initial_delay_seconds": 1, "multiplier": 2, "max_delay_seconds": 5}
    )
    retry_error_taxonomy_mismatch = (
        source_retryable.retry is True
        and source_retryable.delay_ms == 100
        and source_permanent.retry is False
        and target_retry.delay_after_failure(1) == 1
        and not hasattr(target_retry, "retryable_errors")
    )

    # human-in-the-loop-queue: a source-approved queue record does not satisfy the
    # target's bound approval requirements (job version + action + principal + capability).
    as_of = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    source_approval_record = hitl.build_queue_record(
        {
            "request_id": "req-1",
            "expires_at": "2026-08-18T13:00:00Z",
            "decision": "approved",
            "audit": [{"action": "approved", "actor": "alice", "at": "2026-08-18T11:00:00Z"}],
        },
        as_of=as_of,
    )
    target_job = {"id": "req-1", "version": 1, "action": "deploy", "state": "pending", "spent": 0, "budget": 1}
    target_unbound = transition(
        target_job,
        "approved",
        principal="alice",
        capabilities=["approve"],
        current_state={"id": "req-1", "version": 1, "state": "pending"},
        approval=None,
    )
    approval_binding_mismatch = source_approval_record["decision"] == "approved" and target_unbound.get("reason") == "approval_missing"

    # idempotency-kit: result is not part of the source decision rule. Two changed
    # result bodies pass with the same request_id/fingerprint; durable submit binds
    # the complete canonical request and conflicts on changed payload.
    source_idempotent_a = idempotency.evaluate({"request_id": "req", "fingerprint": "fp", "result": {"value": 1}})
    source_idempotent_b = idempotency.evaluate({"request_id": "req", "fingerprint": "fp", "result": {"value": 2}})
    target_idempotency_conflict = _target_idempotency_conflict()
    idempotency_binding_mismatch = (
        source_idempotent_a["status"] == "passed"
        and source_idempotent_b["status"] == "passed"
        and target_idempotency_conflict
    )

    # taskgraph: source GraphSpec owns path scopes and rejects overlapping ownership.
    # The durable workflow schema has no path_scope field; its DAG is a different contract.
    task_base = {
        "owner": "worker",
        "description": "synthetic",
        "dependencies": [],
        "max_attempts": 2,
        "required_evidence": ["test"],
    }
    source_path_conflict_blocked = False
    try:
        taskgraph.GraphSpec.from_dict(
            {
                "schema_version": "1.0",
                "graph_id": "compat",
                "version": "1.0.0",
                "tasks": [
                    {**task_base, "task_id": "a", "path_scope": ["src/shared.py"]},
                    {**task_base, "task_id": "b", "path_scope": ["src/shared.py"]},
                ],
            }
        )
    except Exception:
        source_path_conflict_blocked = True
    target_dag_without_path_ownership_passed = False
    try:
        WorkflowDefinition.from_dict(_workflow(steps=[_step("a"), _step("b")]))
        target_dag_without_path_ownership_passed = True
    except ModelError:
        pass
    taskgraph_contract_mismatch = source_path_conflict_blocked and target_dag_without_path_ownership_passed

    # timeout-toolkit: source explicitly accepts floating-point millisecond evidence;
    # durable workflow timeouts reject floats and model an execution deadline, not an
    # external elapsed-time attestation.
    source_float_timeout = timeout.evaluate({"timeout_ms": 1.5, "elapsed_ms": 1.0, "operation": "synthetic"})
    target_float_timeout_blocked = False
    try:
        StepDefinition.from_dict(_step("float-timeout", timeout_seconds=1.5))
    except ModelError:
        target_float_timeout_blocked = True
    timeout_semantic_mismatch = source_float_timeout["status"] == "passed" and target_float_timeout_blocked

    expected_counterproofs = all(
        (
            budget_dimension_mismatch,
            retry_error_taxonomy_mismatch,
            approval_binding_mismatch,
            idempotency_binding_mismatch,
            taskgraph_contract_mismatch,
            timeout_semantic_mismatch,
        )
    )
    result = {
        "status": "passed" if expected_counterproofs else "failed",
        "kind": "agentops_core_compatibility_probe",
        "sources": {
            "agent-budgeter": {
                "source_sha": BUDGETER_SOURCE_SHA,
                "source_blob_sha": BUDGETER_MODELS_BLOB_SHA,
                "source_bool_budget_accepted": source_bool_budget_accepted,
                "target_bool_budget_blocked": target_bool_budget_blocked,
                "semantic_mismatch_detected": budget_dimension_mismatch,
                "migration_gate": "blocked",
                "reason": "source models a calls/time_ms/tokens vector and accepts bool through Python int semantics; target uses strict integer budget units plus separate timeout/retry controls",
            },
            "agent-retry-kit": {
                "source_sha": RETRY_SOURCE_SHA,
                "source_blob_sha": RETRY_CORE_BLOB_SHA,
                "source_retryable_delay_ms": source_retryable.delay_ms,
                "source_permanent_retry": source_permanent.retry,
                "target_delay_seconds": target_retry.delay_after_failure(1),
                "semantic_mismatch_detected": retry_error_taxonomy_mismatch,
                "migration_gate": "blocked",
                "reason": "source includes retryable error classes and millisecond backoff; durable target RetryPolicy is integer-seconds scheduling without source error taxonomy",
            },
            "human-in-the-loop-queue": {
                "source_sha": HITL_SOURCE_SHA,
                "source_blob_sha": HITL_CORE_BLOB_SHA,
                "source_approved_record": source_approval_record["decision"] == "approved",
                "target_unbound_reason": target_unbound.get("reason"),
                "semantic_mismatch_detected": approval_binding_mismatch,
                "migration_gate": "blocked",
                "reason": "source approval record lacks target workflow/action/version binding and target capability checks; source-approved is not durable authorization evidence",
            },
            "idempotency-kit": {
                "source_sha": IDEMPOTENCY_SOURCE_SHA,
                "source_blob_sha": IDEMPOTENCY_CORE_BLOB_SHA,
                "source_changed_results_both_pass": source_idempotent_a["status"] == source_idempotent_b["status"] == "passed",
                "target_changed_payload_conflict": target_idempotency_conflict,
                "semantic_mismatch_detected": idempotency_binding_mismatch,
                "migration_gate": "blocked",
                "reason": "source rule validates stable nonempty request_id/fingerprint but ignores result content; durable submit binds the complete canonical request digest",
            },
            "taskgraph": {
                "source_sha": TASKGRAPH_SOURCE_SHA,
                "source_blob_sha": TASKGRAPH_MODELS_BLOB_SHA,
                "source_path_conflict_blocked": source_path_conflict_blocked,
                "target_dag_without_path_ownership_passed": target_dag_without_path_ownership_passed,
                "semantic_mismatch_detected": taskgraph_contract_mismatch,
                "migration_gate": "blocked",
                "reason": "source owns path scopes and required evidence kinds; durable workflow DAG has different handler/capability/approval semantics and no path ownership field",
            },
            "timeout-toolkit": {
                "source_sha": TIMEOUT_SOURCE_SHA,
                "source_blob_sha": TIMEOUT_CORE_BLOB_SHA,
                "source_float_timeout_status": source_float_timeout["status"],
                "target_float_timeout_blocked": target_float_timeout_blocked,
                "semantic_mismatch_detected": timeout_semantic_mismatch,
                "migration_gate": "blocked",
                "reason": "source validates external elapsed_ms against timeout_ms and accepts floats; durable target models integer-second execution deadlines and rejects floats",
            },
        },
        "source_blob_match": source_blob_match,
        "target_blob_match": target_blob_match,
        "migration_performed": False,
        "legacy_aliases_activated": False,
        "expected_counterproofs_present": expected_counterproofs,
    }
    result["evidence_sha256"] = sha256(_canonical(result).encode()).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute exact-source compatibility counter-proofs for AgentOps capabilities deduplicated into core.")
    parser.add_argument("--budgeter-root", type=Path, required=True)
    parser.add_argument("--retry-root", type=Path, required=True)
    parser.add_argument("--hitl-root", type=Path, required=True)
    parser.add_argument("--idempotency-root", type=Path, required=True)
    parser.add_argument("--taskgraph-root", type=Path, required=True)
    parser.add_argument("--timeout-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run(
        args.budgeter_root,
        args.retry_root,
        args.hitl_root,
        args.idempotency_root,
        args.taskgraph_root,
        args.timeout_root,
        args.target_root,
    )
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
