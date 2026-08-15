# Agent Quota Simulator

## Purpose

Deterministically admit prioritized synthetic tasks against token, time, and micro-cost budgets.

## Non-goals

It does not run agents, reserve provider quota, predict actual cost, or enforce limits outside the returned plan.

## Install

Requires Python 3.11 or newer.

```console
python -m pip install .
```

## CLI and API

Run the built-in positive and negative control:

```console
agent-quota-simulator probe
```

Process JSON from a file:

```console
agent-quota-simulator simulate --input examples/basic.json
```

The public Python seam is `agent_quota_simulator.simulate`:

```python
from agent_quota_simulator import simulate
```

Functions return structured JSON-compatible results and reject malformed input without raising validation exceptions.

## Example

A runnable input is provided at `examples/basic.json`. CLI output is deterministic and includes either a SHA-256 evidence field or an explicit validation failure.

## Security and trust model

Budgets and tasks are untrusted structured input. IDs must be unique and all numeric fields are bounded non-boolean integers; no coercion occurs. The tool performs no network calls.

## Limitations

At most 1,000 tasks and bounded aggregate demand are evaluated in one simulation.

## Tests

Run the same local gates used by CI:

```console
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
agent-quota-simulator probe
agent-quota-simulator simulate --input examples/basic.json
```

CI tests Python 3.11 and 3.12, installs the project and rebuilt wheel, imports the installed package, and exercises both the probe and example.

## AI disclosure

AI assistance supported defensive implementation, adversarial test design, and documentation. See [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for scope and review expectations.

## License

Apache-2.0. See [LICENSE](LICENSE).

