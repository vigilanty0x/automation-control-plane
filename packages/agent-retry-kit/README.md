# Agent Retry Kit

Deterministic bounded retry policies with fail-closed exhaustion.

Offline Python 3.11+ MVP with zero runtime dependencies, deterministic JSON evidence, bounded inputs, a CLI, synthetic tests, and fail-visible errors.

## Usage

```bash
python -m agent_retry_kit.cli input.json
python -m unittest discover -s tests -v
python scripts/check.py
```

Input is a JSON object matching the public `run(data)` API in `agent_retry_kit.core`. With no path, the CLI reads stdin.

Apache License 2.0.

