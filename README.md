# TaskGraph

TaskGraph is a dependency-free DAG scheduler for agent missions with explicit ownership, isolated path scopes, durable SQLite state, bounded leases, retries, resume, idempotent events, and machine-readable evidence gates.

It never executes task commands or contacts a provider. Workers claim tasks and report evidence through the API/CLI.

```bash
python -m pip install .
taskgraph validate --graph examples/graph.json
taskgraph init --graph examples/graph.json --db reports/taskgraph.db
taskgraph claim --db reports/taskgraph.db --graph-id public-example --worker worker-a --now 100
taskgraph probe --level functional
```

A task reaches `done` only while leased to the reporting worker and after every required evidence kind is present. Failed tasks retry only up to their declared maximum. Expired leases resume safely; downstream tasks become `rejected` when a dependency is terminally failed.

All examples are synthetic. Do not publish private paths, client identities, credentials, internal topology, proprietary logs, or production artifacts.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check.py
PIP_NO_INDEX=1 python -m pip wheel . --no-deps --no-build-isolation
```

Apache-2.0 licensed.

