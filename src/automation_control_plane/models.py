"""Strict, versioned workflow models for the durable control plane.

The format is intentionally small and deterministic.  Unknown fields are
rejected so misspellings cannot silently weaken governance controls.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"
MAX_JSON_BYTES = 64_000
MAX_STEPS = 256
MAX_TRIGGERS = 32
MAX_BUDGET_UNITS = 1_000_000_000
MAX_DEADLINE_SECONDS = 31_536_000
MAX_TIMEOUT_SECONDS = 86_400
MAX_ATTEMPTS = 20
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ModelError(ValueError):
    """Raised when an external workflow document violates the contract."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting ambiguous duplicate members."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ModelError(f"non-standard JSON constant is not accepted: {value}")


def parse_json(text: str, *, label: str = "JSON", maximum_bytes: int = MAX_JSON_BYTES) -> Any:
    """Parse bounded JSON without last-key-wins ambiguity."""

    if not isinstance(text, str) or len(text.encode("utf-8")) > maximum_bytes:
        raise ModelError(f"{label} exceeds byte limit")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_constant,
        )
        _validate_json_value(parsed)
        return parsed
    except json.JSONDecodeError as exc:
        raise ModelError(f"invalid {label}: {exc.msg}") from exc
    except RecursionError as exc:
        raise ModelError(f"invalid {label}: nesting exceeds parser limit") from exc


def canonical_json(value: Any) -> str:
    """Return stable JSON after validating the bounded JSON value."""

    try:
        normalized = _thaw_json(value)
        _validate_json_value(normalized)
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except RecursionError as exc:
        raise ModelError("JSON value is too deeply nested") from exc
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ModelError("JSON value exceeds byte limit")
    return encoded


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _validate_json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > 10_000:
        raise ModelError("JSON value contains too many nodes")
    if depth > 16:
        raise ModelError("JSON value is too deeply nested")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value.encode("utf-8")) > 8_192:
            raise ModelError("JSON string exceeds byte limit")
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
            raise ModelError("JSON integer exceeds interoperable range")
        return
    if isinstance(value, float):
        raise ModelError("floating-point values are not accepted; use integer units")
    if isinstance(value, list):
        if len(value) > 1_000:
            raise ModelError("JSON array exceeds item limit")
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        if len(value) > 1_000:
            raise ModelError("JSON object exceeds member limit")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 256:
                raise ModelError("JSON object keys must be bounded nonempty strings")
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    raise ModelError(f"unsupported JSON type: {type(value).__name__}")


def _strict_object(value: Any, required: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ModelError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ModelError(f"{label} keys must be strings")
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if unknown:
            detail.append(f"unknown={unknown}")
        raise ModelError(f"{label} has invalid fields ({', '.join(detail)})")
    return value


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ModelError(f"{label} must match {_IDENTIFIER.pattern}")
    return value


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ModelError(f"{label} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class TriggerDefinition:
    type: str
    event: str | None = None
    interval_seconds: int | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "TriggerDefinition":
        if not isinstance(value, dict) or "type" not in value:
            raise ModelError("trigger must be an object containing type")
        kind = value["type"]
        if kind == "manual":
            _strict_object(value, {"type"}, label="manual trigger")
            return cls(type=kind)
        if kind == "webhook":
            item = _strict_object(value, {"type", "event"}, label="webhook trigger")
            return cls(type=kind, event=_identifier(item["event"], label="webhook event"))
        if kind == "scheduled":
            item = _strict_object(value, {"type", "interval_seconds"}, label="scheduled trigger")
            interval = _bounded_int(
                item["interval_seconds"], label="scheduled interval_seconds", minimum=1, maximum=MAX_DEADLINE_SECONDS
            )
            return cls(type=kind, interval_seconds=interval)
        raise ModelError("trigger type must be manual, webhook, or scheduled")

    def to_dict(self) -> dict[str, Any]:
        if self.type == "manual":
            return {"type": "manual"}
        if self.type == "webhook":
            return {"type": "webhook", "event": self.event}
        return {"type": "scheduled", "interval_seconds": self.interval_seconds}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_delay_seconds: int
    multiplier: int
    max_delay_seconds: int

    @classmethod
    def from_dict(cls, value: Any) -> "RetryPolicy":
        item = _strict_object(
            value,
            {"max_attempts", "initial_delay_seconds", "multiplier", "max_delay_seconds"},
            label="retry policy",
        )
        maximum = _bounded_int(item["max_attempts"], label="max_attempts", minimum=1, maximum=MAX_ATTEMPTS)
        initial = _bounded_int(
            item["initial_delay_seconds"], label="initial_delay_seconds", minimum=0, maximum=MAX_DEADLINE_SECONDS
        )
        multiplier = _bounded_int(item["multiplier"], label="retry multiplier", minimum=1, maximum=100)
        delay_max = _bounded_int(
            item["max_delay_seconds"], label="max_delay_seconds", minimum=0, maximum=MAX_DEADLINE_SECONDS
        )
        if delay_max < initial:
            raise ModelError("max_delay_seconds cannot be below initial_delay_seconds")
        return cls(maximum, initial, multiplier, delay_max)

    def delay_after_failure(self, attempts: int) -> int:
        exponent = max(0, attempts - 1)
        return min(self.max_delay_seconds, self.initial_delay_seconds * (self.multiplier**exponent))

    def to_dict(self) -> dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "initial_delay_seconds": self.initial_delay_seconds,
            "multiplier": self.multiplier,
            "max_delay_seconds": self.max_delay_seconds,
        }


@dataclass(frozen=True, slots=True)
class StepDefinition:
    id: str
    handler: str
    depends_on: tuple[str, ...]
    input: Mapping[str, Any]
    required_capability: str
    approval: str
    estimated_cost: int
    timeout_seconds: int
    retry: RetryPolicy

    @classmethod
    def from_dict(cls, value: Any) -> "StepDefinition":
        item = _strict_object(
            value,
            {
                "id",
                "handler",
                "depends_on",
                "input",
                "required_capability",
                "approval",
                "estimated_cost",
                "timeout_seconds",
                "retry",
            },
            label="step",
        )
        step_id = _identifier(item["id"], label="step id")
        handler = _identifier(item["handler"], label="handler")
        if not isinstance(item["depends_on"], list) or len(item["depends_on"]) > MAX_STEPS:
            raise ModelError("depends_on must be a bounded list")
        dependencies = tuple(_identifier(dep, label="dependency id") for dep in item["depends_on"])
        if len(dependencies) != len(set(dependencies)) or step_id in dependencies:
            raise ModelError("dependencies must be unique and cannot include the step itself")
        if not isinstance(item["input"], dict):
            raise ModelError("step input must be an object")
        canonical_json(item["input"])
        capability = _identifier(item["required_capability"], label="required capability")
        if item["approval"] not in {"none", "required"}:
            raise ModelError("approval must be none or required")
        cost = _bounded_int(item["estimated_cost"], label="estimated_cost", minimum=0, maximum=MAX_BUDGET_UNITS)
        timeout = _bounded_int(item["timeout_seconds"], label="timeout_seconds", minimum=1, maximum=MAX_TIMEOUT_SECONDS)
        return cls(
            id=step_id,
            handler=handler,
            depends_on=dependencies,
            input=_freeze_json(item["input"]),
            required_capability=capability,
            approval=item["approval"],
            estimated_cost=cost,
            timeout_seconds=timeout,
            retry=RetryPolicy.from_dict(item["retry"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handler": self.handler,
            "depends_on": list(self.depends_on),
            "input": _thaw_json(self.input),
            "required_capability": self.required_capability,
            "approval": self.approval,
            "estimated_cost": self.estimated_cost,
            "timeout_seconds": self.timeout_seconds,
            "retry": self.retry.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    schema_version: str
    workflow_id: str
    version: int
    description: str
    budget_units: int
    default_deadline_seconds: int
    triggers: tuple[TriggerDefinition, ...]
    steps: tuple[StepDefinition, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "WorkflowDefinition":
        item = _strict_object(
            value,
            {
                "schema_version",
                "workflow_id",
                "version",
                "description",
                "budget_units",
                "default_deadline_seconds",
                "triggers",
                "steps",
            },
            label="workflow",
        )
        if item["schema_version"] != SCHEMA_VERSION:
            raise ModelError(f"unsupported schema_version; expected {SCHEMA_VERSION}")
        workflow_id = _identifier(item["workflow_id"], label="workflow id")
        version = _bounded_int(item["version"], label="workflow version", minimum=1, maximum=2**31 - 1)
        description = item["description"]
        if not isinstance(description, str) or len(description.encode("utf-8")) > 4_096:
            raise ModelError("description must be a bounded string")
        budget = _bounded_int(item["budget_units"], label="budget_units", minimum=0, maximum=MAX_BUDGET_UNITS)
        deadline = _bounded_int(
            item["default_deadline_seconds"],
            label="default_deadline_seconds",
            minimum=1,
            maximum=MAX_DEADLINE_SECONDS,
        )
        if not isinstance(item["triggers"], list) or not 1 <= len(item["triggers"]) <= MAX_TRIGGERS:
            raise ModelError("triggers must be a nonempty bounded list")
        triggers = tuple(TriggerDefinition.from_dict(trigger) for trigger in item["triggers"])
        trigger_keys = [(trigger.type, trigger.event, trigger.interval_seconds) for trigger in triggers]
        if len(trigger_keys) != len(set(trigger_keys)):
            raise ModelError("triggers must be unique")
        if not isinstance(item["steps"], list) or not 1 <= len(item["steps"]) <= MAX_STEPS:
            raise ModelError("steps must be a nonempty bounded list")
        steps = tuple(StepDefinition.from_dict(step) for step in item["steps"])
        cls._validate_dag(steps)
        if sum(step.estimated_cost for step in steps) > budget:
            raise ModelError("sum of estimated step costs exceeds workflow budget")
        result = cls(SCHEMA_VERSION, workflow_id, version, description, budget, deadline, triggers, steps)
        canonical_json(result.to_dict())
        return result

    @staticmethod
    def _validate_dag(steps: tuple[StepDefinition, ...]) -> None:
        ids = [step.id for step in steps]
        if len(ids) != len(set(ids)):
            raise ModelError("step ids must be unique")
        known = set(ids)
        for step in steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ModelError(f"step {step.id} references unknown dependencies: {sorted(unknown)}")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {step.id: step for step in steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ModelError("workflow graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for identifier in ids:
            visit(identifier)

    @classmethod
    def from_json(cls, text: str) -> "WorkflowDefinition":
        return cls.from_dict(parse_json(text, label="workflow JSON"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "description": self.description,
            "budget_units": self.budget_units,
            "default_deadline_seconds": self.default_deadline_seconds,
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "steps": [step.to_dict() for step in self.steps],
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())

    def step(self, step_id: str) -> StepDefinition:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def accepts_trigger(self, trigger: Mapping[str, Any]) -> bool:
        supplied = TriggerDefinition.from_dict(dict(trigger))
        return supplied in self.triggers
