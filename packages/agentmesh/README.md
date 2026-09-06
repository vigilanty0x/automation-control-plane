# AgentMesh

Validate multi-agent routes, health, and ownership evidence.

## Quick start

```bash
python -m pip install -e .
agentmesh record.json
```

The CLI emits deterministic fail-closed JSON plus a SHA-256 evidence identifier. Required fields: `agent_count`, `healthy_agents`, `route_count`. Rule: every agent must be healthy and at least one route must exist.

## Verify

```bash
python -m unittest discover -s tests -v
python scripts/check.py
```

Apache-2.0. Python 3.11+. Zero runtime dependencies.

