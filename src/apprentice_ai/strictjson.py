"""Bounded RFC JSON helpers used on every untrusted boundary."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import ValidationError

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DEPTH = 40
DEFAULT_MAX_NODES = 200_000


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc


def _reject_constant(value: str) -> None:
    raise ValidationError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_shape(value: Any, *, max_depth: int, max_nodes: int) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValidationError("JSON node limit exceeded")
        if depth > max_depth:
            raise ValidationError("JSON nesting limit exceeded")
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValidationError("non-finite JSON number is forbidden")


def loads_bytes(
    payload: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> Any:
    if len(payload) > max_bytes:
        raise ValidationError(f"JSON input exceeds {max_bytes} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("JSON input is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ValidationError:
        raise
    except RecursionError as exc:
        raise ValidationError("JSON nesting limit exceeded during parsing") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    except ValueError as exc:
        raise ValidationError(f"invalid JSON numeric value: {exc}") from exc
    _check_shape(value, max_depth=max_depth, max_nodes=max_nodes)
    return value


def load_file(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Any:
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise ValidationError("JSON path must be a regular non-symlink file")
        size = candidate.stat().st_size
        if size > max_bytes:
            raise ValidationError(f"JSON file exceeds {max_bytes} bytes")
        with candidate.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ValidationError(f"cannot read JSON file: {exc}") from exc
    return loads_bytes(payload, max_bytes=max_bytes)
