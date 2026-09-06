# Agent Inbox

Agent Inbox is a dependency-free Python library and CLI for durable, bounded mission
coordination. SQLite transactions ensure that one queued mission has at most one
active lease, while explicit capabilities, permissions, ownership, retries, evidence,
disagreements, and escalations remain inspectable after restart.

Mission states are `queued`, `running`, `waiting`, `failed`, `rejected`, and `done`.
Higher numeric priority is claimed first. `done` is fail-closed: it requires at least
one passed test plus a commit or SHA-256-addressed artefact, and rejects failed or
skipped test evidence.

## Offline example

```bash
DB=/tmp/agent-inbox.sqlite3
PYTHONPATH=src python -m agent_inbox init --db "$DB"
PYTHONPATH=src python -m agent_inbox register --db "$DB" --input examples/agent.json
PYTHONPATH=src python -m agent_inbox enqueue --db "$DB" --input examples/mission.json
PYTHONPATH=src python -m agent_inbox claim --db "$DB" --agent worker --lease-seconds 60
```

Use the returned `mission_id` and `lease_token` with `heartbeat`, `complete`, `wait`,
`fail`, or `reject`. Run the complete offline counter-proof with:

```bash
PYTHONPATH=src python -m agent_inbox probe functional
```

## Guarantees

- `BEGIN IMMEDIATE` claim/recovery transactions prevent duplicate active claims;
- idempotency keys return the original mission or reject conflicting content;
- capabilities, permissions, ownership, concurrency, and maximum lease are enforced;
- expired leases requeue exactly once until the retry budget is exhausted;
- stale lease tokens cannot complete reclaimed work;
- proofless or partially failed work cannot become `done`;
- disagreements and escalations are idempotent append-only events;
- commits, tests, artefacts, evidence SHA, attempts, revisions, and events are visible;
- JSON files are limited to 1 MB; mission payloads to 100 KB; list output to 200 rows.

See `docs/CONTRACT.md`, `docs/STATE_MACHINE.md`, `docs/RECOVERY.md`, and
`docs/SAFETY.md`.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check.py
PIP_NO_INDEX=1 python -m pip wheel . --no-deps --no-build-isolation -w dist
```

Licensed under Apache-2.0.

