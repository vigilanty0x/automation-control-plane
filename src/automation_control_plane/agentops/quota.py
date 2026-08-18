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
    expect_str,
)

MAX_TASKS = 1_000
MAX_BUDGET = 1_000_000_000_000
_RESOURCE_NAMES = ("tokens", "time_ms", "micro_cost")


def simulate_quota(payload: Any) -> dict[str, Any]:
    try:
        return _simulate_quota(payload)
    except ValidationError as exc:
        return blocked("quota_simulation", payload, exc)


def _simulate_quota(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(root, required=("budgets", "tasks"))
    budget_obj = expect_object(root["budgets"], "$.budgets")
    expect_exact_keys(budget_obj, required=_RESOURCE_NAMES, path="$.budgets")
    budgets = {
        name: expect_int(
            budget_obj[name], f"$.budgets.{name}", minimum=0, maximum=MAX_BUDGET
        )
        for name in _RESOURCE_NAMES
    }

    raw_tasks = expect_list(root["tasks"], "$.tasks", maximum=MAX_TASKS)
    tasks: list[dict[str, Any]] = []
    for index, raw_task in enumerate(raw_tasks):
        path = f"$.tasks[{index}]"
        task = expect_object(raw_task, path)
        expect_exact_keys(
            task,
            required=(
                "id",
                "priority",
                "required",
                "tokens",
                "time_ms",
                "micro_cost",
            ),
            path=path,
        )
        tasks.append(
            {
                "id": expect_str(task["id"], f"{path}.id", identifier=True),
                "priority": expect_int(
                    task["priority"],
                    f"{path}.priority",
                    minimum=-1_000,
                    maximum=1_000,
                ),
                "required": expect_bool(task["required"], f"{path}.required"),
                **{
                    name: expect_int(
                        task[name], f"{path}.{name}", minimum=0, maximum=MAX_BUDGET
                    )
                    for name in _RESOURCE_NAMES
                },
            }
        )
    ensure_unique((task["id"] for task in tasks), "$.tasks")

    required_tasks = sorted(
        (task for task in tasks if task["required"]), key=lambda task: task["id"]
    )
    optional_tasks = sorted(
        (task for task in tasks if not task["required"]),
        key=lambda task: (-task["priority"], task["id"]),
    )
    required_totals = {
        name: sum(task[name] for task in required_tasks) for name in _RESOURCE_NAMES
    }
    deficits = {
        name: required_totals[name] - budgets[name]
        for name in _RESOURCE_NAMES
        if required_totals[name] > budgets[name]
    }
    if deficits:
        return evidence(
            "quota_simulation",
            "failed",
            payload,
            {
                "budgets": budgets,
                "required_totals": required_totals,
                "deficits": deficits,
                "admitted": [],
                "rejected": [task["id"] for task in required_tasks + optional_tasks],
                "reason": "required task demand exceeds one or more budgets",
            },
        )

    used = dict(required_totals)
    admitted = list(required_tasks)
    rejected: list[dict[str, Any]] = []
    for task in optional_tasks:
        fits = all(used[name] + task[name] <= budgets[name] for name in _RESOURCE_NAMES)
        if fits:
            admitted.append(task)
            for name in _RESOURCE_NAMES:
                used[name] += task[name]
        else:
            rejected.append(task)
    remaining = {name: budgets[name] - used[name] for name in _RESOURCE_NAMES}
    return evidence(
        "quota_simulation",
        "passed",
        payload,
        {
            "budgets": budgets,
            "used": used,
            "remaining": remaining,
            "admitted": [task["id"] for task in admitted],
            "rejected": [task["id"] for task in rejected],
            "admitted_tasks": admitted,
            "selection_rule": "all required tasks, then optional tasks by descending priority and id",
            "enforcement_scope": "simulation_only",
        },
    )
