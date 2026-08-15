# Human-in-the-Loop Queue

Validate expirable approval requests with decisions and audit history.

## Quick start

```bash
python -m pip install -e .
human-in-the-loop-queue examples/valid.json
```

The command emits deterministic fail-closed JSON and a SHA-256 evidence identifier. It uses synthetic input and has zero runtime dependencies.

## Verify

```bash
python -m unittest discover -s tests -v
python scripts/check.py
```

Apache-2.0. Python 3.11+.

