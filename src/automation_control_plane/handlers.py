"""Explicit registry of in-process, side-effect-bounded handlers.

There is deliberately no shell, subprocess, dynamic import, eval, HTTP, or
filesystem handler. Applications may register reviewed callables at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .models import canonical_json


class HandlerError(RuntimeError):
    """A safe handler rejected or failed an operation."""


@dataclass(frozen=True, slots=True)
class HandlerContext:
    job_id: str
    workflow_id: str
    workflow_version: int
    step_id: str
    attempt: int
    deadline_at: str
    dry_run: bool
    input: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HandlerResult:
    output: Mapping[str, Any]
    cost_units: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.cost_units, bool) or not isinstance(self.cost_units, int) or self.cost_units < 0:
            raise HandlerError("handler cost_units must be a nonnegative integer")
        if not isinstance(self.output, Mapping):
            raise HandlerError("handler output must be an object")
        normalized = json.loads(canonical_json(dict(self.output)))
        object.__setattr__(self, "output", MappingProxyType(normalized))


Handler = Callable[[HandlerContext], HandlerResult]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._capabilities: dict[str, str] = {}

    def register(self, name: str, handler: Handler, *, required_capability: str | None = None) -> None:
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ValueError("handler name must be bounded and nonempty")
        if name in self._handlers:
            raise ValueError(f"handler already registered: {name}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        capability = required_capability or f"handler:{name}"
        if not isinstance(capability, str) or not capability or len(capability.encode("utf-8")) > 256:
            raise ValueError("handler capability must be bounded and nonempty")
        self._handlers[name] = handler
        self._capabilities[name] = capability

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def required_capability(self, name: str) -> str:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise HandlerError(f"handler is not registered: {name}") from exc

    def execute(self, name: str, context: HandlerContext) -> HandlerResult:
        try:
            handler = self._handlers[name]
        except KeyError as exc:
            raise HandlerError(f"handler is not registered: {name}") from exc
        isolated = HandlerContext(
            job_id=context.job_id,
            workflow_id=context.workflow_id,
            workflow_version=context.workflow_version,
            step_id=context.step_id,
            attempt=context.attempt,
            deadline_at=context.deadline_at,
            dry_run=context.dry_run,
            input=MappingProxyType(json.loads(canonical_json(context.input))),
        )
        result = handler(isolated)
        if not isinstance(result, HandlerResult):
            raise HandlerError("handler must return HandlerResult")
        return result


def _noop(context: HandlerContext) -> HandlerResult:
    return HandlerResult({"status": "noop", "step_id": context.step_id}, cost_units=0)


def _emit(context: HandlerContext) -> HandlerResult:
    return HandlerResult({"emitted": dict(context.input)}, cost_units=1)


def _json_merge(context: HandlerContext) -> HandlerResult:
    objects = context.input.get("objects")
    if not isinstance(objects, list) or not all(isinstance(item, dict) for item in objects):
        raise HandlerError("json.merge expects input.objects to be a list of objects")
    merged: dict[str, Any] = {}
    for item in objects:
        merged.update(item)
    return HandlerResult({"merged": merged}, cost_units=1)


def _assert_equals(context: HandlerContext) -> HandlerResult:
    if set(context.input) != {"actual", "expected"}:
        raise HandlerError("assert.equals expects exactly actual and expected")
    if context.input["actual"] != context.input["expected"]:
        raise HandlerError("assertion failed")
    return HandlerResult({"equal": True}, cost_units=0)


def builtin_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register("noop", _noop)
    registry.register("emit", _emit)
    registry.register("json.merge", _json_merge)
    registry.register("assert.equals", _assert_equals)
    return registry
