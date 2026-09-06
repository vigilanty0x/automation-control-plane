from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model_router.models import TaskRequest
from model_router.router import ModelRouter
from model_router.store import MissionRecord, SQLiteMissionStore


@dataclass(frozen=True, slots=True)
class Submission:
    mission: MissionRecord
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {"created": self.created, "mission": self.mission.to_dict()}


class ModelRouterService:
    """Thin orchestration boundary joining routing and durable queueing."""

    def __init__(self, router: ModelRouter, store: SQLiteMissionStore) -> None:
        self.router = router
        self.store = store

    def submit(self, task: TaskRequest) -> Submission:
        decision = self.router.route(task)
        mission, created = self.store.enqueue(task, decision)
        return Submission(mission=mission, created=created)
