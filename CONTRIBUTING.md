# Contributing

## Ground rules

Changes must preserve these invariants:

1. JSON cannot provide its own trusted identity, capabilities, approval authority, or kill authority.
2. Workflows are data, never executable code.
3. Unknown schema fields and ambiguous numeric values fail closed.
4. Worker execution is limited to explicitly registered reviewed handlers.
5. Authorization and current-state checks happen inside the same transaction as mutation.
6. Accepted state, audit event, and outbox envelope commit atomically.
7. Registry-bound capabilities cannot be weakened by workflow data.
8. Approval, lease generation, deadline, reserved budget, and kill checks remain effective at completion, not only claim.
9. Every terminal job contains only terminal steps and no live lease or budget reservation; recovery must not resurrect it.
10. Audit anchors and successful-result receipts are verified before backup/restore.
11. Existing `run`, `simulate`, `transition`, and legacy CLI behavior remain compatible unless a major release explicitly changes them.

## Development

Use Python 3.11 or newer. Runtime dependencies are not accepted without a design discussion showing why the standard library cannot meet the requirement and how supply-chain risk is controlled.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
python scripts/check.py
python -m pip install "build==1.2.2.post1" "setuptools==80.9.0" "wheel==0.45.1"
python -m build --no-isolation
```

## Change expectations

- Add a focused test demonstrating the behavior or prior gap.
- Include negative and recovery cases for authorization, storage, time, concurrency, or execution changes.
- Use synthetic fixtures only; never commit credentials, private data, or production topology.
- Keep errors bounded and machine-readable at the CLI boundary.
- Document public schema/API/CLI changes and update `CHANGELOG.md`.
- Inspect the complete diff and run the full suite from a clean source install or built wheel.

## Handler review

A new handler should represent one narrow capability, independently validate its typed input, be idempotent at any external side-effect boundary, honor `context.deadline_at`, report integer cost units, and return bounded JSON. Generic shell, subprocess, URL fetch, SQL, dynamic import, evaluator, and arbitrary filesystem handlers are out of scope.

## Pull requests

Explain the threat/failure model, public compatibility impact, database migration behavior, and exact verification commands. CI must remain least privilege and every third-party action must be pinned to a full commit SHA.
