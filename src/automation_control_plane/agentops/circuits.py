from __future__ import annotations

from typing import Any

from ._common import (
    ValidationError,
    blocked,
    ensure_unique,
    evidence,
    expect_exact_keys,
    expect_int,
    expect_list,
    expect_object,
    expect_str,
)

MAX_EVENTS = 10_000
_STATES = {"closed", "open", "half_open"}
_OUTCOMES = {"success", "failure", "cooldown_elapsed"}


def simulate_circuit(payload: Any) -> dict[str, Any]:
    try:
        return _simulate_circuit(payload)
    except ValidationError as exc:
        return blocked("circuit_simulation", payload, exc)


def _simulate_circuit(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(root, required=("policy", "initial_state", "events"))
    policy_obj = expect_object(root["policy"], "$.policy")
    expect_exact_keys(
        policy_obj,
        required=("failure_threshold", "success_threshold", "max_events"),
        path="$.policy",
    )
    failure_threshold = expect_int(
        policy_obj["failure_threshold"],
        "$.policy.failure_threshold",
        minimum=1,
        maximum=1_000,
    )
    success_threshold = expect_int(
        policy_obj["success_threshold"],
        "$.policy.success_threshold",
        minimum=1,
        maximum=1_000,
    )
    max_events = expect_int(
        policy_obj["max_events"], "$.policy.max_events", minimum=1, maximum=MAX_EVENTS
    )
    initial_state = expect_str(root["initial_state"], "$.initial_state", maximum=16)
    if initial_state not in _STATES:
        raise ValidationError("$.initial_state: invalid circuit state")
    raw_events = expect_list(root["events"], "$.events", maximum=max_events)
    events: list[dict[str, str]] = []
    for index, raw_event in enumerate(raw_events):
        path = f"$.events[{index}]"
        event = expect_object(raw_event, path)
        expect_exact_keys(event, required=("id", "outcome"), path=path)
        event_id = expect_str(event["id"], f"{path}.id", identifier=True)
        outcome = expect_str(event["outcome"], f"{path}.outcome", maximum=32)
        if outcome not in _OUTCOMES:
            raise ValidationError(f"{path}.outcome: invalid circuit outcome")
        events.append({"id": event_id, "outcome": outcome})
    ensure_unique((event["id"] for event in events), "$.events")

    state = initial_state
    failure_count = 0
    success_count = 0
    trace: list[dict[str, Any]] = []
    invalid_transition: dict[str, Any] | None = None
    for index, event in enumerate(events):
        before = state
        outcome = event["outcome"]
        if state == "closed":
            if outcome == "success":
                failure_count = 0
            elif outcome == "failure":
                failure_count += 1
                if failure_count >= failure_threshold:
                    state = "open"
                    failure_count = 0
                    success_count = 0
            else:
                invalid_transition = {
                    "index": index,
                    "event_id": event["id"],
                    "reason": "cooldown_elapsed_is_only_valid_while_open",
                }
        elif state == "open":
            if outcome == "cooldown_elapsed":
                state = "half_open"
                failure_count = 0
                success_count = 0
            else:
                invalid_transition = {
                    "index": index,
                    "event_id": event["id"],
                    "reason": "open_state_requires_cooldown_elapsed",
                }
        else:
            if outcome == "success":
                success_count += 1
                if success_count >= success_threshold:
                    state = "closed"
                    success_count = 0
                    failure_count = 0
            elif outcome == "failure":
                state = "open"
                success_count = 0
                failure_count = 0
            else:
                invalid_transition = {
                    "index": index,
                    "event_id": event["id"],
                    "reason": "cooldown_elapsed_is_not_valid_while_half_open",
                }
        trace.append(
            {
                "index": index,
                "event_id": event["id"],
                "outcome": outcome,
                "state_before": before,
                "state_after": state,
                "failure_count": failure_count,
                "success_count": success_count,
            }
        )
        if invalid_transition:
            break
    status = "failed" if invalid_transition else "passed"
    return evidence(
        "circuit_simulation",
        status,
        payload,
        {
            "initial_state": initial_state,
            "final_state": state,
            "processed_event_count": len(trace),
            "invalid_transition": invalid_transition,
            "trace": trace,
            "enforcement_scope": "simulation_only",
        },
    )
