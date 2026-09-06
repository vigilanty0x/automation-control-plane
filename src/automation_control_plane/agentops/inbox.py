from __future__ import annotations

from typing import Any

from ._common import (
    ValidationError,
    blocked,
    ensure_unique,
    evidence,
    expect_bool,
    expect_exact_keys,
    expect_int,
    expect_list,
    expect_object,
    expect_optional_str,
    expect_str,
)

MAX_JOBS = 5_000
_STATES = {
    "queued",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "cancelled",
}


def project_inbox(payload: Any) -> dict[str, Any]:
    try:
        return _project_inbox(payload)
    except ValidationError as exc:
        return blocked("operator_inbox", payload, exc)


def _project_inbox(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(root, required=("now_epoch_ms", "jobs"))
    now = expect_int(
        root["now_epoch_ms"], "$.now_epoch_ms", minimum=0, maximum=10**16
    )
    raw_jobs = expect_list(root["jobs"], "$.jobs", maximum=MAX_JOBS)
    jobs: list[dict[str, Any]] = []
    for index, raw_job in enumerate(raw_jobs):
        path = f"$.jobs[{index}]"
        job = expect_object(raw_job, path)
        expect_exact_keys(
            job,
            required=(
                "job_id",
                "workflow_id",
                "state",
                "priority",
                "deadline_epoch_ms",
                "approval_required",
                "last_error",
            ),
            path=path,
        )
        state = expect_str(job["state"], f"{path}.state", maximum=32)
        if state not in _STATES:
            raise ValidationError(f"{path}.state: invalid job state")
        deadline = job["deadline_epoch_ms"]
        if deadline is not None:
            deadline = expect_int(
                deadline, f"{path}.deadline_epoch_ms", minimum=0, maximum=10**16
            )
        jobs.append(
            {
                "job_id": expect_str(job["job_id"], f"{path}.job_id", identifier=True),
                "workflow_id": expect_str(
                    job["workflow_id"], f"{path}.workflow_id", identifier=True
                ),
                "state": state,
                "priority": expect_int(
                    job["priority"], f"{path}.priority", minimum=-1_000, maximum=1_000
                ),
                "deadline_epoch_ms": deadline,
                "approval_required": expect_bool(
                    job["approval_required"], f"{path}.approval_required"
                ),
                "last_error": expect_optional_str(
                    job["last_error"], f"{path}.last_error", maximum=512
                ),
            }
        )
    ensure_unique((job["job_id"] for job in jobs), "$.jobs")

    items: list[dict[str, Any]] = []
    terminal_count = 0
    for job in jobs:
        state = job["state"]
        deadline = job["deadline_epoch_ms"]
        overdue = deadline is not None and deadline <= now
        if state in {"succeeded", "cancelled"}:
            terminal_count += 1
            continue
        if overdue:
            action = "cancel_or_reconcile_deadline"
            urgency = 100
        elif state == "failed":
            action = "investigate_failure"
            urgency = 90
        elif state == "waiting_approval":
            action = "review_approval"
            urgency = 80
        elif state == "queued":
            action = "dispatch_or_cancel"
            urgency = 50
        else:
            action = "observe_or_reconcile"
            urgency = 20
        if job["approval_required"] and state != "waiting_approval":
            action = "reconcile_approval_state"
            urgency = max(urgency, 85)
        items.append(
            {
                **job,
                "overdue": overdue,
                "recommended_action": action,
                "urgency": urgency,
            }
        )
    items.sort(
        key=lambda item: (
            -item["urgency"],
            -item["priority"],
            item["deadline_epoch_ms"] if item["deadline_epoch_ms"] is not None else 10**16,
            item["job_id"],
        )
    )
    return evidence(
        "operator_inbox",
        "passed",
        payload,
        {
            "now_epoch_ms": now,
            "input_job_count": len(jobs),
            "terminal_job_count": terminal_count,
            "actionable_item_count": len(items),
            "items": items,
            "mode": "read_only_projection",
            "mutation_performed": False,
        },
    )
