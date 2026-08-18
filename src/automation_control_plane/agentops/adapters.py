from __future__ import annotations

from datetime import datetime
from typing import Any

from ._common import (
    ValidationError,
    blocked,
    evidence,
    expect_bool,
    expect_exact_keys,
    expect_int,
    expect_list,
    expect_object,
    expect_str,
)
from .circuits import simulate_circuit
from .context import plan_context
from .quota import simulate_quota
from .routing import evaluate_routing
from .sessions import record_session

ADAPTER_SCHEMA_VERSION = "1.0"

_SOURCE_SHAS = {
    "agentmesh": "320f5116f6582519d1609ce87287fd9ff7267eb3",
    "context-window-budgeter": "35bb3e05d05ad870715b740143c429f08eda25e7",
    "agent-quota-simulator": "e99000cecf12432365e8ccfc8fa6e4b1d18ad15f",
    "agent-session-recorder": "2363c4efe0c61158c523a6dfc3d29cb3d7af1c54",
    "circuit-breaker-lab": "2924dfb6eed8a208788491fa1d50fa6bd99e4359",
}


def _base_details(source: str) -> dict[str, Any]:
    return {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "source_repository": source,
        "source_sha": _SOURCE_SHAS[source],
        "rehearsal_only": True,
        "alias_activated": False,
        "migration_performed": False,
        "consumer_mutation_performed": False,
        "source_retirement_authorized": False,
    }


def _source_context_plan(payload: dict[str, Any]) -> dict[str, Any]:
    window = expect_int(payload["window_tokens"], "$.source_payload.window_tokens", minimum=1, maximum=10_000_000)
    reserve = expect_int(payload["output_reserve"], "$.source_payload.output_reserve", minimum=0, maximum=10_000_000)
    if reserve >= window:
        raise ValidationError("$.source_payload.output_reserve: must be below window_tokens")
    raw_sections = expect_list(payload["sections"], "$.source_payload.sections", maximum=500)
    names: set[str] = set()
    sections: list[dict[str, Any]] = []
    aggregate = 0
    for index, raw in enumerate(raw_sections):
        path = f"$.source_payload.sections[{index}]"
        section = expect_object(raw, path)
        expect_exact_keys(section, required=("name", "tokens", "required"), optional=("priority",), path=path)
        name = expect_str(section["name"], f"{path}.name", maximum=64, identifier=True)
        if name in names:
            raise ValidationError(f"{path}.name: duplicate section")
        names.add(name)
        tokens = expect_int(section["tokens"], f"{path}.tokens", minimum=0, maximum=10_000_000)
        aggregate += tokens
        if aggregate > 10_000_000:
            raise ValidationError("$.source_payload.sections: aggregate token bound exceeded")
        sections.append({
            "name": name,
            "tokens": tokens,
            "required": expect_bool(section["required"], f"{path}.required"),
            "priority": expect_int(section.get("priority", 0), f"{path}.priority", minimum=0, maximum=100),
        })
    available = window - reserve
    required = sorted((item for item in sections if item["required"]), key=lambda item: item["name"])
    optional = sorted((item for item in sections if not item["required"]), key=lambda item: (-item["priority"], item["name"]))
    used = sum(item["tokens"] for item in required)
    if used > available:
        return {"status": "failed", "selected": [], "dropped": [item["name"] for item in required + optional], "used": used, "available": available}
    selected = [item["name"] for item in required]
    dropped: list[str] = []
    for item in optional:
        if used + item["tokens"] <= available:
            selected.append(item["name"])
            used += item["tokens"]
        else:
            dropped.append(item["name"])
    return {"status": "passed", "selected": selected, "dropped": dropped, "used": used, "available": available, "required": required, "optional": optional}


def _agentmesh_adapter(root: dict[str, Any]) -> dict[str, Any]:
    source = expect_object(root["source_payload"], "$.source_payload")
    expect_exact_keys(source, required=("agent_count", "healthy_agents", "route_count"), path="$.source_payload")
    agent_count = expect_int(source["agent_count"], "$.source_payload.agent_count", minimum=1, maximum=256)
    healthy_agents = expect_int(source["healthy_agents"], "$.source_payload.healthy_agents", minimum=0, maximum=256)
    route_count = expect_int(source["route_count"], "$.source_payload.route_count", minimum=1, maximum=4096)
    source_passed = healthy_agents == agent_count
    target_payload = expect_object(root["adapter_input"], "$.adapter_input")
    target = evaluate_routing(target_payload)
    details = target.get("details", {}) if isinstance(target, dict) else {}
    count_match = (
        details.get("agent_count") == agent_count
        and details.get("healthy_agent_count") == healthy_agents
        and details.get("active_route_count") == route_count
    )
    passed = source_passed and target.get("status") == "passed" and count_match
    return evidence(
        "adapter_rehearsal",
        "passed" if passed else "failed",
        root,
        {
            **_base_details("agentmesh"),
            "adapter": "explicit_identity_route_mapping",
            "source_semantics_passed": source_passed,
            "target_routing_status": target.get("status"),
            "count_match": count_match,
            "target_result": target,
            "rule": "caller supplies explicit agent identities, owners, capabilities and routes; source counts are never promoted into authorization identity",
        },
    )


def _context_adapter(root: dict[str, Any]) -> dict[str, Any]:
    source = expect_object(root["source_payload"], "$.source_payload")
    expect_exact_keys(source, required=("window_tokens", "output_reserve", "sections"), path="$.source_payload")
    source_plan = _source_context_plan(source)
    if source_plan["status"] != "passed":
        return evidence(
            "adapter_rehearsal",
            "failed",
            root,
            {**_base_details("context-window-budgeter"), "adapter": "source_order_preserving_context", "source_plan": source_plan, "reason": "source input itself is not admissible"},
        )
    optional_order = [item["name"] for item in source_plan["optional"]]
    rank = {name: index for index, name in enumerate(optional_order)}
    target_sections = []
    for raw in source["sections"]:
        name = raw["name"]
        required = raw["required"]
        # The target's optional tie-break includes token size. Assigning a unique
        # adapter-only priority by exact source order removes that semantic drift.
        target_priority = 0 if required else 1000 - rank[name]
        target_sections.append({
            "id": name,
            "tokens": raw["tokens"],
            "required": required,
            "priority": target_priority,
        })
    target_payload = {
        "window_tokens": source["window_tokens"],
        "reserve_output_tokens": source["output_reserve"],
        "sections": target_sections,
    }
    target = plan_context(target_payload)
    details = target.get("details", {})
    selection_match = details.get("included") == source_plan["selected"] and details.get("excluded") == source_plan["dropped"]
    passed = target.get("status") == "passed" and selection_match
    return evidence(
        "adapter_rehearsal",
        "passed" if passed else "failed",
        root,
        {
            **_base_details("context-window-budgeter"),
            "adapter": "source_order_preserving_context",
            "source_plan": {key: source_plan[key] for key in ("selected", "dropped", "used", "available")},
            "target_payload": target_payload,
            "target_result": target,
            "selection_match": selection_match,
            "priority_translation": "optional sections receive unique adapter-only priorities derived from exact source (-priority,name) order",
        },
    )


def _quota_adapter(root: dict[str, Any]) -> dict[str, Any]:
    source = expect_object(root["source_payload"], "$.source_payload")
    expect_exact_keys(source, required=("budget", "tasks"), path="$.source_payload")
    budget = expect_object(source["budget"], "$.source_payload.budget")
    expect_exact_keys(budget, required=("tokens", "seconds", "cost_micros"), path="$.source_payload.budget")
    source_budget = {
        "tokens": expect_int(budget["tokens"], "$.source_payload.budget.tokens", minimum=0, maximum=1_000_000_000_000),
        "seconds": expect_int(budget["seconds"], "$.source_payload.budget.seconds", minimum=0, maximum=1_000_000_000_000),
        "cost_micros": expect_int(budget["cost_micros"], "$.source_payload.budget.cost_micros", minimum=0, maximum=1_000_000_000_000),
    }
    if source_budget["seconds"] > 1_000_000_000:
        raise ValidationError("$.source_payload.budget.seconds: exact seconds-to-ms conversion exceeds target bound")
    raw_tasks = expect_list(source["tasks"], "$.source_payload.tasks", maximum=1_000)
    seen: set[str] = set()
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks):
        path = f"$.source_payload.tasks[{index}]"
        task = expect_object(raw, path)
        expect_exact_keys(task, required=("id", "tokens", "seconds", "cost_micros"), optional=("priority",), path=path)
        task_id = expect_str(task["id"], f"{path}.id", maximum=64, identifier=True)
        if task_id in seen:
            raise ValidationError(f"{path}.id: duplicate task")
        seen.add(task_id)
        seconds = expect_int(task["seconds"], f"{path}.seconds", minimum=0, maximum=1_000_000_000_000)
        if seconds > 1_000_000_000:
            raise ValidationError(f"{path}.seconds: exact seconds-to-ms conversion exceeds target bound")
        tasks.append({
            "id": task_id,
            "priority": expect_int(task.get("priority", 0), f"{path}.priority", minimum=0, maximum=100),
            "tokens": expect_int(task["tokens"], f"{path}.tokens", minimum=0, maximum=1_000_000_000_000),
            "seconds": seconds,
            "cost_micros": expect_int(task["cost_micros"], f"{path}.cost_micros", minimum=0, maximum=1_000_000_000_000),
        })
    ordered = sorted(tasks, key=lambda item: (-item["priority"], item["id"]))
    remaining = dict(source_budget)
    source_admitted: list[str] = []
    source_rejected: list[str] = []
    for task in ordered:
        if all(task[name] <= remaining[name] for name in ("tokens", "seconds", "cost_micros")):
            source_admitted.append(task["id"])
            for name in ("tokens", "seconds", "cost_micros"):
                remaining[name] -= task[name]
        else:
            source_rejected.append(task["id"])
    target_payload = {
        "budgets": {
            "tokens": source_budget["tokens"],
            "time_ms": source_budget["seconds"] * 1000,
            "micro_cost": source_budget["cost_micros"],
        },
        "tasks": [
            {
                "id": task["id"],
                "priority": task["priority"],
                "required": False,
                "tokens": task["tokens"],
                "time_ms": task["seconds"] * 1000,
                "micro_cost": task["cost_micros"],
            }
            for task in tasks
        ],
    }
    target = simulate_quota(target_payload)
    target_details = target.get("details", {})
    selection_match = target_details.get("admitted") == source_admitted and target_details.get("rejected") == source_rejected
    remaining_match = target_details.get("remaining") == {
        "tokens": remaining["tokens"],
        "time_ms": remaining["seconds"] * 1000,
        "micro_cost": remaining["cost_micros"],
    }
    passed = target.get("status") == "passed" and selection_match and remaining_match
    return evidence(
        "adapter_rehearsal",
        "passed" if passed else "failed",
        root,
        {
            **_base_details("agent-quota-simulator"),
            "adapter": "exact_units_optional_task_mapping",
            "source_admitted": source_admitted,
            "source_rejected": source_rejected,
            "target_payload": target_payload,
            "target_result": target,
            "selection_match": selection_match,
            "remaining_match": remaining_match,
            "unit_mapping": {"seconds": "time_ms * 1000", "cost_micros": "micro_cost identity", "tokens": "tokens identity"},
            "required_task_semantics": "disabled because the source contract has no required-task concept",
        },
    )


def _session_adapter(root: dict[str, Any]) -> dict[str, Any]:
    source = expect_object(root["source_payload"], "$.source_payload")
    expect_exact_keys(source, required=("events",), path="$.source_payload")
    adapter_input = expect_object(root["adapter_input"], "$.adapter_input")
    expect_exact_keys(adapter_input, required=("session_id", "actor", "timestamps"), path="$.adapter_input")
    session_id = expect_str(adapter_input["session_id"], "$.adapter_input.session_id", identifier=True)
    actor = expect_str(adapter_input["actor"], "$.adapter_input.actor", identifier=True)
    raw_events = expect_list(source["events"], "$.source_payload.events", maximum=10_000)
    timestamps = expect_list(adapter_input["timestamps"], "$.adapter_input.timestamps", maximum=10_000)
    if len(timestamps) != len(raw_events):
        raise ValidationError("$.adapter_input.timestamps: must contain exactly one timestamp per source event")
    target_events: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_events):
        path = f"$.source_payload.events[{index}]"
        event = expect_object(raw, path)
        expect_exact_keys(event, required=("sequence", "kind", "content"), path=path)
        sequence = expect_int(event["sequence"], f"{path}.sequence", minimum=1, maximum=10_000)
        if sequence != index + 1:
            raise ValidationError(f"{path}.sequence: source sequence must be contiguous from 1")
        kind = expect_str(event["kind"], f"{path}.kind", maximum=32)
        if kind not in {"input", "output", "tool", "decision", "error"}:
            raise ValidationError(f"{path}.kind: unsupported source event kind")
        timestamp = expect_str(timestamps[index], f"$.adapter_input.timestamps[{index}]", maximum=64)
        try:
            parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
        except ValueError as exc:
            raise ValidationError(f"$.adapter_input.timestamps[{index}]: invalid ISO-8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValidationError(f"$.adapter_input.timestamps[{index}]: timezone-aware timestamp required")
        target_events.append({
            "event_id": f"legacy-{sequence}",
            "actor": actor,
            "type": kind,
            "at": timestamp,
            "data": {"legacy_content": event["content"], "legacy_sequence": sequence},
        })
    target_payload = {"session_id": session_id, "events": target_events}
    target = record_session(target_payload)
    passed = target.get("status") == "passed"
    return evidence(
        "adapter_rehearsal",
        "passed" if passed else "failed",
        root,
        {
            **_base_details("agent-session-recorder"),
            "adapter": "explicit_actor_timestamp_session_enrichment",
            "target_payload": target_payload,
            "target_result": target,
            "source_event_count": len(raw_events),
            "target_event_count": len(target_events),
            "content_transformation": "source content is nested unchanged under data.legacy_content; sensitive target keys therefore fail closed instead of being silently redacted",
            "authenticity_transferred": False,
            "source_head_reused": False,
        },
    )


def _circuit_adapter(root: dict[str, Any]) -> dict[str, Any]:
    source = expect_object(root["source_payload"], "$.source_payload")
    expect_exact_keys(source, required=("events",), optional=("threshold", "cooldown_ms"), path="$.source_payload")
    threshold = expect_int(source.get("threshold", 3), "$.source_payload.threshold", minimum=1, maximum=1_000)
    cooldown_ms = expect_int(source.get("cooldown_ms", 1000), "$.source_payload.cooldown_ms", minimum=0, maximum=1_000_000_000)
    raw_events = expect_list(source["events"], "$.source_payload.events", maximum=10_000)
    normalized: list[dict[str, Any]] = []
    previous_at = -1
    for index, raw in enumerate(raw_events):
        path = f"$.source_payload.events[{index}]"
        event = expect_object(raw, path)
        expect_exact_keys(event, required=("at_ms", "success"), path=path)
        at_ms = expect_int(event["at_ms"], f"{path}.at_ms", minimum=0, maximum=1_000_000_000_000)
        if at_ms < previous_at:
            raise ValidationError(f"{path}.at_ms: adapter requires nondecreasing externally observed time")
        previous_at = at_ms
        normalized.append({"at_ms": at_ms, "success": expect_bool(event["success"], f"{path}.success")})

    state = "closed"
    failures = 0
    opened_at: int | None = None
    target_events: list[dict[str, str]] = []
    source_trace: list[dict[str, Any]] = []
    suppressed = 0
    for index, event in enumerate(normalized):
        allowed = True
        if state == "open":
            if event["at_ms"] - (opened_at or 0) >= cooldown_ms:
                target_events.append({"id": f"legacy-cooldown-{index + 1}", "outcome": "cooldown_elapsed"})
                state = "half_open"
            else:
                allowed = False
        if allowed:
            outcome = "success" if event["success"] else "failure"
            target_events.append({"id": f"legacy-outcome-{index + 1}", "outcome": outcome})
            if event["success"]:
                failures = 0
                opened_at = None
                state = "closed"
            else:
                failures += 1
                if state == "half_open" or failures >= threshold:
                    state = "open"
                    opened_at = event["at_ms"]
        else:
            suppressed += 1
        source_trace.append({"index": index, "allowed": allowed, "state": state})
    if len(target_events) > 10_000:
        raise ValidationError("$.source_payload.events: translated target event count exceeds target bound")
    target_payload = {
        "policy": {"failure_threshold": threshold, "success_threshold": 1, "max_events": max(1, len(target_events))},
        "initial_state": "closed",
        "events": target_events,
    }
    target = simulate_circuit(target_payload)
    target_final = target.get("details", {}).get("final_state")
    final_match = target.get("status") == "passed" and target_final == state
    return evidence(
        "adapter_rehearsal",
        "passed" if final_match else "failed",
        root,
        {
            **_base_details("circuit-breaker-lab"),
            "adapter": "externally_evidenced_cooldown_events",
            "source_trace": source_trace,
            "source_final_state": state,
            "suppressed_open_state_attempts": suppressed,
            "target_payload": target_payload,
            "target_result": target,
            "final_state_match": final_match,
            "cooldown_rule": "an explicit cooldown_elapsed target event is emitted only when supplied monotonic at_ms evidence proves the source cooldown elapsed",
        },
    )


def rehearse_adapter(payload: Any) -> dict[str, Any]:
    try:
        root = expect_object(payload)
        expect_exact_keys(root, required=("source_repository", "source_sha", "source_payload"), optional=("adapter_input",))
        source = expect_str(root["source_repository"], "$.source_repository", maximum=64)
        if source not in _SOURCE_SHAS:
            raise ValidationError("$.source_repository: source has no candidate_adapter contract")
        source_sha = expect_str(root["source_sha"], "$.source_sha", maximum=40)
        if source_sha != _SOURCE_SHAS[source]:
            raise ValidationError("$.source_sha: does not match the reviewed candidate-adapter source SHA")
        if source == "agentmesh":
            if "adapter_input" not in root:
                raise ValidationError("$.adapter_input: explicit target identity mapping is required")
            return _agentmesh_adapter(root)
        if source == "context-window-budgeter":
            return _context_adapter(root)
        if source == "agent-quota-simulator":
            return _quota_adapter(root)
        if source == "agent-session-recorder":
            if "adapter_input" not in root:
                raise ValidationError("$.adapter_input: actor, session and timestamps are required")
            return _session_adapter(root)
        return _circuit_adapter(root)
    except ValidationError as exc:
        return blocked("adapter_rehearsal", payload, exc)
