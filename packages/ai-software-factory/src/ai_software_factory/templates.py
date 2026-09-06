"""Compile declared workflow templates into the single native FactorySpec.

Substitution derives from workflow-templates (Apache-2.0), commit
f210986b2fa7917c1b70ce0f82b10f23e87f63b7. Execution fields are deliberately
stricter: only names, descriptions and environment values accept bindings.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from .models import FactorySpec, SpecError

CATALOG_FORMAT = "ai-software-factory/template-catalog-v1"
ORIGIN_FORMAT = "ai-software-factory/template-origin-v1"
COMPILER = "nonstructural-v1"
MAX_CATALOG_BYTES = 1_048_576
MAX_BINDINGS_BYTES = 65_536
MAX_ORIGIN_BYTES = 2_097_152
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
VARIABLE = re.compile(r"\{\{([A-Z][A-Z0-9_]{0,63})\}\}")


def _json(value: Any, maximum: int) -> str:
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 200_000:
            raise SpecError("template JSON structural limit exceeded")
        if type(item) is dict:
            if any(not isinstance(key, str) for key in item):
                raise SpecError("template JSON keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) not in {str, int, float, bool, type(None)}:
            raise SpecError("template input must contain only JSON types")
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        size = len(result.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise SpecError("template input must be finite bounded JSON") from exc
    if size > maximum:
        raise SpecError("template JSON byte limit exceeded")
    return result


def _hash(value: Any, maximum: int = MAX_ORIGIN_BYTES) -> str:
    return sha256(_json(value, maximum).encode("utf-8")).hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SpecError("duplicate template JSON key")
        value[key] = item
    return value


def read_json(source: str | bytes, *, maximum: int) -> Any:
    if not isinstance(source, (str, bytes)):
        raise SpecError("template JSON source must be text or bytes")
    try:
        if len(source.encode("utf-8") if isinstance(source, str) else source) > maximum:
            raise SpecError("template JSON byte limit exceeded")
        value = json.loads(source, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(SpecError("non-finite template value")))
        _json(value, maximum)  # Also refuses overflow such as 1e309.
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SpecError("invalid template JSON") from exc


def _placeholders(value: Any, *, allowed: bool = False) -> set[str]:
    if isinstance(value, str):
        variables = set(VARIABLE.findall(value))
        remainder = VARIABLE.sub("", value)
        if "{{" in remainder or "}}" in remainder:
            raise SpecError("invalid template placeholder")
        if variables and not allowed:
            raise SpecError("placeholders are forbidden in execution or structural fields")
        return variables
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            found.update(_placeholders(key))
            found.update(_placeholders(item, allowed=allowed))
        return found
    if isinstance(value, list):
        return set().union(*(_placeholders(item, allowed=allowed) for item in value)) if value else set()
    return set()


def _normalize_template(raw: Any) -> tuple[dict[str, Any], set[str]]:
    spec = FactorySpec.from_dict(raw)
    if len(spec.tasks) > 1000:
        raise SpecError("template exceeds 1000 tasks")
    value = spec.to_dict()
    found = _placeholders(value["name"], allowed=True)
    for key, item in value.items():
        if key not in {"name", "tasks"}:
            _placeholders(item)
    for task in value["tasks"]:
        for key, item in task.items():
            if key == "environment":
                for name, content in item.items():
                    found.update(_placeholders(content, allowed=name.startswith("FACTORY_INPUT_")))
            else:
                found.update(_placeholders(item, allowed=key == "description"))
    if len(found) > 100:
        raise SpecError("template exceeds 100 variables")
    _json(value, MAX_CATALOG_BYTES)
    return value, found


@dataclass(frozen=True, slots=True)
class Compilation:
    spec: FactorySpec
    origin: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"spec": self.spec.to_dict(), "origin": json.loads(_json(self.origin, MAX_ORIGIN_BYTES)),
                "origin_sha256": _hash(self.origin)}


def compile_template(catalog: Any, template_id: str, bindings: Any) -> Compilation:
    """Pure compilation; no file read, process, workspace or Store creation."""
    _json(catalog, MAX_CATALOG_BYTES)
    if type(catalog) is not dict or set(catalog) != {"format", "templates"} or catalog["format"] != CATALOG_FORMAT:
        raise SpecError("unsupported template catalog")
    choices = catalog["templates"]
    if type(choices) is not dict or not 1 <= len(choices) <= 32:
        raise SpecError("template catalog must contain 1..32 templates")
    normalized = {}
    for identifier, raw in choices.items():
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise SpecError("invalid template identifier")
        normalized[identifier] = _normalize_template(raw)
    _json({"format": CATALOG_FORMAT, "templates": {key: item[0] for key, item in normalized.items()}}, MAX_CATALOG_BYTES)
    if not isinstance(template_id, str) or template_id not in normalized:
        raise SpecError("unknown template identifier")
    template, needed = normalized[template_id]
    _json(bindings, MAX_BINDINGS_BYTES)
    if type(bindings) is not dict or set(bindings) != needed:
        raise SpecError("template bindings must match all and only declared placeholders")
    for key, value in bindings.items():
        if (not isinstance(key, str) or not isinstance(value, str)
                or len(value.encode("utf-8")) > 4096 or "{{" in value or "}}" in value):
            raise SpecError("bindings must be bounded strings without recursive placeholders")
    result = json.loads(_json(template, MAX_CATALOG_BYTES))
    substitute = lambda value: VARIABLE.sub(lambda match: bindings[match.group(1)], value)
    result["name"] = substitute(result["name"])
    for task in result["tasks"]:
        task["description"] = substitute(task["description"])
        task["environment"] = {key: substitute(value) for key, value in task["environment"].items()}
    spec = FactorySpec.from_dict(result)
    origin = {"format": ORIGIN_FORMAT, "compiler": COMPILER, "template_id": template_id,
              "template": template, "bindings": dict(sorted(bindings.items())),
              "template_sha256": _hash(template), "bindings_sha256": _hash(bindings),
              "spec_sha256": sha256(spec.canonical_json().encode("utf-8")).hexdigest(),
              "provenance": "caller_declared_template_recompiled_locally"}
    _json(origin, MAX_ORIGIN_BYTES)
    return Compilation(spec, origin)


def validate_origin(spec: FactorySpec, origin: Any) -> dict[str, Any]:
    """Recompile retained inputs; a digest alone is insufficient evidence."""
    serialized = _json(origin, MAX_ORIGIN_BYTES)
    if type(origin) is not dict or set(origin) != {"format", "compiler", "template_id", "template", "bindings",
                                                  "template_sha256", "bindings_sha256", "spec_sha256", "provenance"}:
        raise SpecError("template origin schema invalid")
    identifier = origin["template_id"]
    if not isinstance(identifier, str):
        raise SpecError("template origin identifier invalid")
    compiled = compile_template({"format": CATALOG_FORMAT, "templates": {identifier: origin["template"]}},
                                identifier, origin["bindings"])
    if _json(compiled.origin, MAX_ORIGIN_BYTES) != serialized or compiled.spec.canonical_json() != spec.canonical_json():
        raise SpecError("template origin does not reproduce the effective specification")
    return json.loads(serialized)


def verify_template_events(spec: FactorySpec, events: list[dict[str, Any]]) -> list[str]:
    if any(not isinstance(event, dict) for event in events):
        return ["template journal contains a malformed event"]
    created = [event for event in events if event.get("event_type") == "run.created"]
    origins = [event for event in events if str(event.get("event_type", "")).startswith("run.template")]
    anchors = [event for event in created if isinstance(event.get("payload"), dict)
               and "template_origin_sha256" in event["payload"]]
    if not origins and not anchors:
        return []
    try:
        if len(created) != 1 or len(origins) != 1 or len(anchors) != 1 or len(events) < 2:
            raise SpecError("template journal count invalid")
        event, anchor = origins[0], anchors[0]
        if (events[0] is not anchor or events[1] is not event or event.get("task_id") is not None
                or event.get("event_type") != "run.template_compiled" or event.get("event_key") != "run.template_compiled"
                or event.get("created_at") != anchor.get("created_at")):
            raise SpecError("template journal ordering invalid")
        origin = validate_origin(spec, event.get("payload"))
        if anchor["payload"]["template_origin_sha256"] != _hash(origin):
            raise SpecError("template journal anchor mismatch")
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return ["template provenance invalid: " + str(exc)]
    return []
