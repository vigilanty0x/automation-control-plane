# Agent Handoff

Agent Handoff is a zero-dependency JSON contract and deterministic Markdown renderer for transferring software missions between agents without hiding ownership, limits, unfinished criteria, evidence, disagreement, or escalation.

## Quick start

```bash
python -m pip install .
agent-handoff validate --input examples/handoff.json
agent-handoff render --input examples/handoff.json --format markdown
agent-handoff append --input examples/handoff.json --ledger reports/handoffs.jsonl
agent-handoff verify-ledger --ledger reports/handoffs.jsonl
agent-handoff probe --level functional
```

## Fail-closed contract

A `done` handoff is valid only when every acceptance criterion is met, machine-readable evidence exists, and no high or critical blocker/escalation is hidden. Repeating the same mission sequence is idempotent; changing it under the same key is a conflict.

The append-only ledger chains every event with SHA-256 and verifies the entire chain before and after writes. JSON and Markdown rendering are deterministic and suitable for diffs, pull requests, and offline export.

## Public boundary

All fixtures use synthetic agents and artifact URIs. Do not publish customer names, private repository paths, internal branch names, credentials, logs containing secrets, or proprietary topology.

See `docs/CONTRACT.md`, `docs/ARCHITECTURE.md`, `docs/SAFETY.md`, and `AI_ASSISTANCE.md`.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check.py
PIP_NO_INDEX=1 python -m pip wheel . --no-deps --no-build-isolation
```

Apache-2.0 licensed.

