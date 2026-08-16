from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def step(
    identifier: str = "step-a",
    *,
    handler: str = "noop",
    depends_on: list[str] | None = None,
    approval: str = "none",
    estimated_cost: int = 0,
    attempts: int = 3,
    delay: int = 0,
    input_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "handler": handler,
        "depends_on": list(depends_on or []),
        "input": dict(input_value or {}),
        "required_capability": f"handler:{handler}",
        "approval": approval,
        "estimated_cost": estimated_cost,
        "timeout_seconds": 30,
        "retry": {
            "max_attempts": attempts,
            "initial_delay_seconds": delay,
            "multiplier": 2,
            "max_delay_seconds": max(delay, delay * 4),
        },
    }


def workflow(
    *,
    workflow_id: str = "test-flow",
    version: int = 1,
    steps: list[dict[str, Any]] | None = None,
    budget: int = 10,
    deadline: int = 300,
    triggers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workflow_id": workflow_id,
        "version": version,
        "description": "Synthetic test workflow.",
        "budget_units": budget,
        "default_deadline_seconds": deadline,
        "triggers": list(triggers or [{"type": "manual"}]),
        "steps": list(steps or [step()]),
    }

