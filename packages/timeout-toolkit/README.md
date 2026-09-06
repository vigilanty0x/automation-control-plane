# Timeout Toolkit

Fail-closed timeout planning and deadline evidence.

## Quick start

```bash
python -m pip install -e .
timeout-toolkit record.json
```

The CLI emits deterministic JSON with a fail-closed status and a SHA-256 evidence identifier. Required fields: `timeout_ms`, `elapsed_ms`, `operation`. Rule: elapsed_ms must not exceed timeout_ms.

## Development

```bash
python -m unittest discover -s tests -v
python scripts/check.py
```

Apache-2.0 licensed. No runtime dependencies.

