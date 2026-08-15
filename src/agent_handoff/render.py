"""Deterministic JSON and GitHub-flavored Markdown renderers."""

from __future__ import annotations

import json

from .models import Handoff


def render_json(handoff: Handoff) -> str:
    payload = {**handoff.to_dict(), "logical_sha256": handoff.logical_sha256}
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(handoff: Handoff) -> str:
    lines = [
        f"# Agent Handoff: {handoff.handoff_id}", "",
        f"- Mission: `{handoff.mission_id}`", f"- Sequence: `{handoff.sequence}`",
        f"- State: `{handoff.state}`", f"- From: `{handoff.from_agent}`",
        f"- To: `{handoff.to_agent}`", f"- Owner: `{handoff.owner}`",
        f"- Created: `{handoff.created_at}`", f"- Logical SHA-256: `{handoff.logical_sha256}`", "",
        "## Summary", "", handoff.summary, "", "## Path scope", "",
    ]
    lines.extend(f"- `{path}`" for path in handoff.path_scope)
    lines.extend(["", "## Acceptance criteria", "", "| Criterion | Met | Description |", "|---|---:|---|"])
    lines.extend(f"| `{item.criterion_id}` | {'yes' if item.met else 'no'} | {_escape(item.description)} |" for item in handoff.criteria)
    lines.extend(["", "## Evidence", ""])
    if handoff.evidence:
        lines.extend(["| Evidence | Kind | SHA-256 | Summary |", "|---|---|---|---|"])
        lines.extend(f"| `{item.evidence_id}` | `{item.kind}` | `{item.sha256}` | {_escape(item.summary)} |" for item in handoff.evidence)
    else:
        lines.append("_No evidence recorded._")
    lines.extend(["", "## Open risks, disagreements, and escalations", ""])
    if handoff.open_items:
        lines.extend(["| Item | Kind | Severity | Description |", "|---|---|---|---|"])
        lines.extend(f"| `{item.item_id}` | `{item.kind}` | `{item.severity}` | {_escape(item.description)} |" for item in handoff.open_items)
    else:
        lines.append("_No open items._")
    lines.extend(["", "## Capabilities and permissions", "", f"- Capabilities: {', '.join(f'`{item}`' for item in handoff.capabilities) or '_none_'}", f"- Permissions: {', '.join(f'`{item}`' for item in handoff.permissions) or '_none_'}", ""])
    return "\n".join(lines)

