# Agent Budgeter

Agent Budgeter is a zero-runtime-dependency, offline budget ledger for agent missions. It controls call count, elapsed milliseconds, and tokens with global, per-mission, and per-agent ceilings. Unknown measurements, overruns, permission failures, and idempotency conflicts fail closed.

## Guarantees

- canonical agent registry with owner, capabilities, permissions, limits, and retry ceiling;
- mission states: `queued`, `running`, `waiting`, `failed`, `rejected`, `done`;
- locked transactional reserve, consume, release, and retry operations;
- deterministic reservation and evidence SHA identifiers;
- append-only, fsynced, idempotent JSONL evidence;
- persistent consumed budget after unused capacity is released;
- metrics for consumption, rejections, interventions, and mission states;
- synthetic fixtures and a counter-proof requiring unknown usage and over-budget work to fail.

## Quick start

```bash
python -m agent_budgeter demo --journal evidence.jsonl
python -m agent_budgeter fixture examples/control.json --journal evidence.jsonl
python -m agent_budgeter probe functional
```

## Python API

```python
from agent_budgeter import AgentProfile, BudgetEngine, BudgetVector, Mission
from agent_budgeter.registry import AgentRegistry

profile = AgentProfile("writer", "team", ("code",), ("local",), BudgetVector(10, 60_000, 50_000), 2)
engine = BudgetEngine(BudgetVector(100, 600_000, 500_000), AgentRegistry((profile,)))
engine.add_mission(Mission("task-1", "writer", "code", "local", BudgetVector(5, 30_000, 20_000)))
result = engine.reserve("op-reserve-1", "task-1", BudgetVector(1, 5_000, 2_000))
```

Every mutating call needs a unique operation ID. Replaying identical input returns the original result without consuming twice. Reusing the same operation ID with different input is blocked and counted as an intervention.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check.py
PYTHONPATH=src python -m agent_budgeter probe functional
PIP_NO_INDEX=1 python -m pip wheel . --no-deps --no-build-isolation
```

Apache License 2.0. See [LICENSE](LICENSE).

