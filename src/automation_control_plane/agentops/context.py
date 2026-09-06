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

MAX_WINDOW_TOKENS = 10_000_000
MAX_SECTIONS = 500


def plan_context(payload: Any) -> dict[str, Any]:
    try:
        return _plan_context(payload)
    except ValidationError as exc:
        return blocked("context_budget", payload, exc)


def _plan_context(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(
        root,
        required=("window_tokens", "reserve_output_tokens", "sections"),
    )
    window = expect_int(
        root["window_tokens"], "$.window_tokens", minimum=1, maximum=MAX_WINDOW_TOKENS
    )
    reserve = expect_int(
        root["reserve_output_tokens"],
        "$.reserve_output_tokens",
        minimum=0,
        maximum=window,
    )
    raw_sections = expect_list(root["sections"], "$.sections", maximum=MAX_SECTIONS)
    sections: list[dict[str, Any]] = []
    for index, raw_section in enumerate(raw_sections):
        path = f"$.sections[{index}]"
        section = expect_object(raw_section, path)
        expect_exact_keys(
            section,
            required=("id", "tokens", "required", "priority"),
            path=path,
        )
        sections.append(
            {
                "id": expect_str(section["id"], f"{path}.id", identifier=True),
                "tokens": expect_int(
                    section["tokens"],
                    f"{path}.tokens",
                    minimum=0,
                    maximum=MAX_WINDOW_TOKENS,
                ),
                "required": expect_bool(section["required"], f"{path}.required"),
                "priority": expect_int(
                    section["priority"],
                    f"{path}.priority",
                    minimum=-1_000,
                    maximum=1_000,
                ),
            }
        )
    ensure_unique((section["id"] for section in sections), "$.sections")

    input_budget = window - reserve
    required = sorted(
        (section for section in sections if section["required"]),
        key=lambda item: item["id"],
    )
    optional = sorted(
        (section for section in sections if not section["required"]),
        key=lambda item: (-item["priority"], item["tokens"], item["id"]),
    )
    required_total = sum(section["tokens"] for section in required)
    if required_total > input_budget:
        details = {
            "window_tokens": window,
            "reserve_output_tokens": reserve,
            "input_budget_tokens": input_budget,
            "required_tokens": required_total,
            "deficit_tokens": required_total - input_budget,
            "included": [],
            "excluded": [section["id"] for section in required + optional],
            "reason": "required sections exceed the bounded input budget",
        }
        return evidence("context_budget", "failed", payload, details)

    included = list(required)
    excluded: list[dict[str, Any]] = []
    used = required_total
    for section in optional:
        if used + section["tokens"] <= input_budget:
            included.append(section)
            used += section["tokens"]
        else:
            excluded.append(section)
    details = {
        "window_tokens": window,
        "reserve_output_tokens": reserve,
        "input_budget_tokens": input_budget,
        "used_input_tokens": used,
        "remaining_input_tokens": input_budget - used,
        "included": [section["id"] for section in included],
        "excluded": [section["id"] for section in excluded],
        "included_sections": included,
        "selection_rule": "required first; optional by descending priority, ascending size, then id",
    }
    return evidence("context_budget", "passed", payload, details)
