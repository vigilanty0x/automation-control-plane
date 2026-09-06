# Idempotency Kit

Deterministic idempotency keys, replay checks, and evidence.

## Quick start

```bash
python -m pip install -e .
idempotency-kit record.json
```

The CLI emits deterministic JSON with a fail-closed status and a SHA-256 evidence identifier. Required fields: `request_id`, `fingerprint`, `result`. Rule: request_id and fingerprint must be stable.

## Development

```bash
python -m unittest discover -s tests -v
python scripts/check.py
```

Apache-2.0 licensed. No runtime dependencies.

