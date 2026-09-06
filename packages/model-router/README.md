# Model Router

An evidence-first router and durable queue for multi-agent work. It selects an agent by capability, permission, budget, latency, context window, load, and historical success, then keeps the mission observable until machine-readable proof permits completion.

This is a standalone public implementation of roadmap item **PUB-006**. It uses the Python standard library at runtime and ships only generic code and synthetic examples.

## Why it exists

Simple routers often choose a model and lose the operational story. Model Router keeps four questions answerable:

1. Why was this agent selected—or every candidate rejected?
2. Who owns the mission, which files are in scope, and what proves success?
3. Can a retry resume without duplicating the mission?
4. Can another machine verify that `done` is justified?

## Features

- capability registry with explicit permissions, limits, ownership, load, and reliability;
- hard rejection boundaries for missing capabilities, permissions, context, budget, latency, saturation, and high-risk access;
- deterministic explainable ranking with visible close-score disagreements;
- states: `queued`, `running`, `waiting`, `failed`, `rejected`, and `done`;
- SQLite queue with unique idempotency keys, FIFO claims, bounded leases, and atomic events;
- explicit recovery for expired worker leases—never a silent orphan;
- bounded retries that preserve the original mission identity;
- human approval gates for high-risk missions;
- mandatory commit, tests, artifacts, producer, and acceptance-criterion evidence before `done`;
- metrics for pass@1, retries per task, rejection rate, human interventions, and wall time;
- JSON CLI with bounded 1 MB inputs and stable exit codes;
- no runtime dependencies, account, network call, or private service.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install -e .
model-router route --agents examples/agents.json --task examples/task.json
model-router demo --db /tmp/model-router-demo.sqlite3
```

The demo submits, claims, proves, and completes one entirely synthetic mission.

## End-to-end CLI journey

```bash
# Route and persist the task. Repeating this command returns the same mission.
model-router enqueue \
  --db router.sqlite3 \
  --agents examples/agents.json \
  --task examples/task.json

# One worker atomically claims the oldest eligible mission.
model-router claim --db router.sqlite3 --worker worker-a --lease-seconds 300

# Completion is rejected unless every declared criterion has proof.
model-router transition \
  --db router.sqlite3 \
  --mission MISSION_ID \
  --to done \
  --actor worker-a \
  --reason "all gates passed" \
  --evidence examples/evidence.json

# Inspect the immutable story and operational measures.
model-router inspect --db router.sqlite3 --mission MISSION_ID
model-router metrics --db router.sqlite3
```

If a worker disappears, make the lease failure visible before retrying:

```bash
model-router recover --db router.sqlite3 --actor lease-reaper
model-router retry --db router.sqlite3 --mission MISSION_ID --actor scheduler
```

## Routing contract

Hard constraints run before scoring. An agent is ineligible when it is inactive, saturated, missing a required capability or permission, too expensive, too slow, or lacks context capacity. High-risk work additionally requires the `high_risk` permission and a recorded human approval before claim.

Eligible candidates receive a score composed of:

| Signal | Weight |
| --- | ---: |
| Historical success | 50% |
| Budget fit | 20% |
| Latency fit | 15% |
| Context headroom | 10% |
| Available concurrency | 5% |

The tie-break is deterministic by agent ID. Candidates within `0.03` remain visible as a disagreement and create an escalation reason.

## Completion contract

`done` is permitted only from `running`, and only with an evidence bundle containing:

- a commit SHA;
- at least one test result;
- at least one artifact path;
- the producing worker;
- a passing boolean for every acceptance criterion declared by the task.

The queue stores the bundle both on the mission and on the transition event.

## Commands and exit codes

| Command | Purpose |
| --- | --- |
| `route` | Explain a decision without persistence |
| `enqueue` | Route and idempotently create a mission |
| `claim` | Atomically lease the oldest eligible mission |
| `approve` | Record a human approval gate |
| `transition` | Apply a validated state transition |
| `retry` | Requeue a failed mission inside its budget |
| `recover` | Expose expired leases as failures |
| `inspect` | Return a mission and its ordered event trail |
| `list` | List all or state-filtered missions |
| `metrics` | Return durable operational measures |
| `demo` | Run the synthetic complete journey |

Exit code `2` means invalid input, `3` means mission not found, and `4` means invalid transition. Successful commands emit JSON to stdout; bounded errors emit JSON to stderr.

## Verification

```bash
python scripts/check.py
python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir /tmp/wheel
```

See [Architecture](docs/ARCHITECTURE.md), [SQLite schema](docs/SCHEMA.md), [Security policy](SECURITY.md), and [AI assistance disclosure](AI_ASSISTANCE.md).

## License

Apache-2.0.
