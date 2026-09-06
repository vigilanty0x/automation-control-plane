from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Iterable

MAX_INPUT_BYTES = 1_000_000
MAX_OUTPUT_BYTES = 2_000_000
MAX_ERROR_MESSAGE = 400
MAX_DEPTH = 12
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(ValueError):
    """Bounded validation error for untrusted structured input."""


def _error(path: str, message: str) -> ValidationError:
    return ValidationError(f"{path}: {message}"[:MAX_ERROR_MESSAGE])


def expect_object(value: Any, path: str = "$") -> dict[str, Any]:
    if type(value) is not dict:
        raise _error(path, "expected object")
    return value


def expect_exact_keys(
    value: dict[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    path: str = "$",
) -> None:
    required_set = set(required)
    optional_set = set(optional)
    missing = sorted(required_set - value.keys())
    unknown = sorted(value.keys() - required_set - optional_set)
    if missing:
        raise _error(path, f"missing keys: {', '.join(missing)}")
    if unknown:
        raise _error(path, f"unknown keys: {', '.join(unknown)}")


def expect_list(value: Any, path: str, *, maximum: int) -> list[Any]:
    if type(value) is not list:
        raise _error(path, "expected array")
    if len(value) > maximum:
        raise _error(path, f"too many items; maximum is {maximum}")
    return value


def expect_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise _error(path, "expected boolean")
    return value


def expect_int(value: Any, path: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise _error(path, "expected integer")
    if value < minimum or value > maximum:
        raise _error(path, f"integer must be between {minimum} and {maximum}")
    return value


def expect_str(
    value: Any,
    path: str,
    *,
    minimum: int = 1,
    maximum: int = 256,
    identifier: bool = False,
) -> str:
    if type(value) is not str:
        raise _error(path, "expected string")
    if len(value) < minimum or len(value) > maximum:
        raise _error(path, f"string length must be between {minimum} and {maximum}")
    if value != value.strip():
        raise _error(path, "leading or trailing whitespace is not allowed")
    if identifier and not _IDENTIFIER.fullmatch(value):
        raise _error(path, "invalid identifier")
    return value


def expect_optional_str(value: Any, path: str, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return expect_str(value, path, maximum=maximum)


def expect_sha256(value: Any, path: str) -> str:
    text = expect_str(value, path, minimum=64, maximum=64)
    if not _HEX_64.fullmatch(text):
        raise _error(path, "expected lowercase SHA-256 hex digest")
    return text


def ensure_unique(values: Iterable[str], path: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise _error(path, f"duplicate value: {value}")
        seen.add(value)


def ensure_json(value: Any, path: str = "$", *, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise _error(path, f"maximum JSON depth is {MAX_DEPTH}")
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        raise _error(path, "floating-point values are not accepted")
    if type(value) is list:
        if len(value) > 10_000:
            raise _error(path, "array exceeds 10000 items")
        for index, item in enumerate(value):
            ensure_json(item, f"{path}[{index}]", depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > 2_000:
            raise _error(path, "object exceeds 2000 members")
        for key, item in value.items():
            if type(key) is not str:
                raise _error(path, "object keys must be strings")
            ensure_json(item, f"{path}.{key}", depth=depth + 1)
        return
    raise _error(path, f"unsupported JSON value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    ensure_json(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def json_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _deduplicating_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _error("$", f"duplicate JSON member: {key}")
        output[key] = value
    return output


def strict_loads(text: str) -> Any:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_deduplicating_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                _error("$", f"non-standard JSON constant: {token}")
            ),
            parse_float=lambda token: (_ for _ in ()).throw(
                _error("$", f"floating-point value is not accepted: {token}")
            ),
        )
    except ValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise _error("$", f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    ensure_json(value)
    return value


def evidence(kind: str, status: str, payload: Any, details: dict[str, Any]) -> dict[str, Any]:
    if status not in {"passed", "failed", "blocked"}:
        raise RuntimeError("invalid internal status")
    ensure_json(details, "$.details")
    try:
        input_digest = json_sha256(payload)
    except ValidationError:
        input_digest = None
    result: dict[str, Any] = {
        "schema_version": "agentops.v1",
        "kind": kind,
        "status": status,
        "input_sha256": input_digest,
        "details": details,
    }
    result["evidence_sha256"] = json_sha256(result)
    if len(canonical_json(result).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise RuntimeError("internal output exceeds bound")
    return result


def blocked(kind: str, payload: Any, exc: Exception) -> dict[str, Any]:
    message = str(exc).replace("\n", " ")[:MAX_ERROR_MESSAGE]
    return evidence(kind, "blocked", payload, {"error": message})
