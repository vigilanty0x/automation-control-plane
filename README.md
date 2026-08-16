# Automation Control Plane

A dependency-free Python 3.11+ control plane for governed, local automation. It turns strict versioned JSON workflows into durable SQLite jobs with DAG dependencies, bound approvals, atomic worker leases, bounded budgets, retries, deadlines, kill switches, recovery, an outbox, and a tamper-evident audit chain.

The project is deliberately an execution *control plane*, not a general-purpose command runner. Workers invoke only explicitly registered Python handlers. The built-in registry has no shell, subprocess, network, dynamic-import, `eval`, or filesystem handler.

## Purpose

Automation often starts as an untracked script and later needs operational guarantees: duplicate requests must not duplicate work, concurrent workers must not claim the same step, approvals must apply to the exact artifact reviewed, crashes must be recoverable, and an emergency stop must actually stop execution. This package provides those governance primitives in one inspectable standard-library implementation.

Highlights:

- strict workflow schema `1.0`; unknown keys, cycles, unbounded values, floats, and invalid dependencies fail closed;
- immutable workflow versions identified by a canonical SHA-256 digest;
- SQLite schema version `2` with `STRICT` tables for workflows, jobs, steps, approvals, RBAC, kill switches, audit events, and an atomic outbox;
- idempotent submissions bound to the complete canonical request, plus optimistic versions for operator decisions;
- `BEGIN IMMEDIATE` claims with unguessable, expiring lease tokens;
- manual, webhook-event, and fixed-interval schedule trigger representations;
- root and downstream step approvals bound to workflow digest, input digest, job version, and step version;
- global and per-workflow kill switches that immediately cancel and fence matching nonterminal jobs;
- integer cost units, atomic pre-execution reservations, per-job limits, settlement, deadlines, bounded retries, and deterministic backoff;
- dry-run jobs that traverse the real DAG and approval flow without invoking handlers;
- expired-lease recovery, deadline enforcement, job-state reconciliation, and outbox repair;
- anchored append-only hash-chain verification, result receipts, and no-follow online backup/validated restore;
- machine-readable CLI plus an optional loopback-only read-only HTTP API/dashboard;
- compatibility with the original `run(data)`, `simulate(job, target)`, and `transition(...)` APIs.

## Non-goals

- It is not an authentication provider. A caller must authenticate the principal before passing its name to the programmatic API or local CLI.
- It does not accept arbitrary code, shell commands, URLs, or executable workflow content.
- It is not a distributed consensus system. SQLite provides strong single-database coordination; multi-region operation is outside scope.
- The webhook and scheduled trigger objects are governed representations. An authenticated ingress or scheduler calls `submit`; this package does not expose a write-capable HTTP endpoint or run a background scheduler.
- The audit hash chain and database-held count/head anchors are tamper-evident, not an external signature or immutable ledger. Export/checkpoint the count and head hash to independent storage when that property is required.

## Install

Requires Python 3.11 or newer and has no runtime dependencies.

```bash
python -m pip install .
automation-control-plane --help
```

For source-tree development:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Quick start

Initialize a database, assign a least-privilege worker, register the example workflow, submit a dry run, and inspect it:

```bash
automation-control-plane init --db control-plane.db
automation-control-plane role --db control-plane.db local-worker worker
automation-control-plane role --db control-plane.db local-approver approver
automation-control-plane register --db control-plane.db examples/flagship_workflow.json
automation-control-plane submit --db control-plane.db synthetic-release \
  --idempotency-key example-001 --dry-run
automation-control-plane list --db control-plane.db jobs
automation-control-plane worker --db control-plane.db --principal local-worker --max-steps 10
```

When `release` reaches `waiting_approval`, use the IDs and current versions returned by `show`:

```bash
automation-control-plane show --db control-plane.db JOB_ID
automation-control-plane approve --db control-plane.db --principal local-approver \
  JOB_ID release --reason "Evidence reviewed" --job-version JOB_VERSION --step-version STEP_VERSION
automation-control-plane worker --db control-plane.db --principal local-worker --max-steps 10
automation-control-plane audit --db control-plane.db
```

Or run the complete synthetic demo in a new initialized database:

```bash
automation-control-plane init --db demo.db
automation-control-plane demo --db demo.db
```

## CLI and API

Every successful CLI command prints one JSON object and exits `0`. Operational/validation failures print bounded JSON and exit `2`. `--help` documents all options.

| Command | Effect |
| --- | --- |
| `init` | Create/upgrade the schema, seed built-in roles, and bootstrap local principal `admin`. |
| `register` | Validate and immutably register a workflow version. |
| `submit` | Create or replay an idempotent job for a declared trigger. |
| `list` | List workflows, jobs, audit events, outbox records, or kill switches. |
| `show` | Return one job with every step and approval record. |
| `approve` / `reject` | Decide a pending, digest-bound approval with optional expected versions. |
| `cancel` | Optimistically cancel a nonterminal job and revoke active leases. |
| `worker` | Claim and execute only registered handlers. |
| `kill` | Version and toggle a global or per-workflow kill switch. |
| `reconcile` | Recover expired leases/deadlines and repair derived job/outbox state. |
| `audit` | Recompute and verify the complete audit hash chain. |
| `backup` / `restore` | Online-backup or integrity-check/atomically restore SQLite. |
| `role` | Assign a built-in role; requires `role:assign`. |
| `serve` | Run the loopback-only read-only dashboard/API. |
| `demo` | Execute a synthetic dry-run DAG including approval. |

### Programmatic durable API

```python
from automation_control_plane import ControlPlane, ControlPlaneStore, WorkflowDefinition

store = ControlPlaneStore("control-plane.db")
store.initialize()
control = ControlPlane(store)

definition = WorkflowDefinition.from_json(open("examples/flagship_workflow.json", encoding="utf-8").read())
control.register_workflow(definition, principal="admin")
job = control.submit(
    "synthetic-release",
    principal="admin",
    trigger={"type": "manual"},
    idempotency_key="request-001",
    payload={"source": "synthetic-example"},
    dry_run=True,
)
```

Applications should create a `HandlerRegistry`, register reviewed callables at startup, grant the worker role only the handlers it needs, then inject that registry into `ControlPlane`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Compatibility API

The JSON-facing `automation_control_plane.core.run(data)` contract remains simulation-only. Existing commands continue to work:

```bash
automation-control-plane examples/basic.json
python -m automation_control_plane.cli examples/basic.json
```

Existing callers of `transition(...)` retain the original trusted-state transition boundary. Durable operations use `ControlPlane`; untrusted JSON still cannot smuggle identity, capabilities, approvals, or kill authority into `run`.

### Read-only dashboard

```bash
automation-control-plane serve --db control-plane.db --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/`. The server rejects non-loopback binds and all write methods. Endpoints are `/health`, `/api/audit`, `/api/workflows`, `/api/jobs`, `/api/jobs/{id}`, `/api/events`, and `/api/kill-switches`. It is intentionally not an authenticated remote admin interface.

## Example workflow

See [examples/flagship_workflow.json](examples/flagship_workflow.json) and [docs/WORKFLOW_SCHEMA.md](docs/WORKFLOW_SCHEMA.md). Every field is required and unknown fields are rejected. Values are integer units; floating-point budget/cost values are never accepted.

## Security and trust model

The default policy is deny-by-default. `admin` is bootstrapped only during local database initialization. The built-in `operator`, `approver`, `worker`, `dispatcher`, and `viewer` roles have separate capabilities. Role assignment is itself authorized and audited. Direct database write access remains equivalent to control-plane administrator access.

Worker safety depends on a narrow registry. A workflow names a handler, but it cannot define or import it. Trusted startup code binds each registered handler to a capability (default `handler:<name>`); a workflow cannot weaken that binding. Claiming requires `job:claim`, the registry capability, and the workflow's additional `required_capability`. An unavailable or unauthorized handler is never executed.

Approvals are bound to the current workflow, input, job version, and step version and checked again at lease time. Lease completion requires matching owner/token, fence generation, and an unexpired lease. Enabling a kill switch immediately terminalizes matching jobs and increments their fence generation; disabling it never revives an old lease.

Read [SECURITY.md](SECURITY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) before using the package for material operations.

## Operations

Use `audit`, `reconcile`, and `backup` regularly. Worker processes may safely restart; an expired lease is retried only within attempt/deadline bounds. Estimates are reserved atomically at claim and released or settled on every accepted terminal/retry path. The outbox records the same committed event that changed state, so downstream publishers can deliver at least once and acknowledge by lease token via the programmatic API.

Detailed runbooks, failure behavior, backup restoration, and SQLite constraints are in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Limitations

- In-process Python handlers cannot be forcibly terminated safely. The engine enforces deadlines before claim and at completion; handlers must also cooperate with the deadline in `HandlerContext`. Isolate untrusted or non-cooperative work in a separately governed service.
- SQLite serializes writers. That is intentional for atomic decisions and is appropriate for a local/single-node control plane, not unlimited write throughput.
- Role-to-capability definitions are seeded in SQLite. Changing them is currently an explicit database administration/migration operation; the public CLI only assigns built-in roles.
- Audit verification detects event mutation, sequence/tail deletion against database-held anchors, successful-result mutation against its digest and success event, and outbox-envelope drift from its source event. An attacker with full database access could still rewrite all records and anchors; external `(event_count, head_hash)` checkpointing is required for stronger non-repudiation.
- Workflow payloads are stored. Do not submit secrets, private data, credentials, or production records unless the surrounding deployment supplies the necessary data governance and encryption.

## Tests

Run the full local contract:

```bash
python -m unittest discover -s tests -v
python scripts/check.py
python -m pip install "build==1.2.2.post1" "setuptools==80.9.0" "wheel==0.45.1"
python -m build --no-isolation
```

The test suite covers the compatibility API, schema strictness, DAG cycles, idempotency, concurrent submission/claim, trusted handler capability binding, approval version binding/rejection/self-approval, optimistic conflicts, retries, terminal fencing, lock-delayed clocks, crash recovery, stale leases, deadlines, dry-run isolation, atomic budget reservations, kill epochs, starvation resistance, outbox repair, audit/result/tail tampering, no-follow backup/validated restore, CLI flows, and read-only HTTP behavior.

CI uses read-only repository permissions, commit-pinned actions, exact build-tool versions, Python 3.11/3.12, wheel installation, the full suite, repository boundary checks, and installed CLI smoke tests.

## AI assistance

Material AI-assisted design and validation disclosures are documented in [AI_ASSISTANCE.md](AI_ASSISTANCE.md). AI output is not treated as a security attestation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Public compatibility, strict parsing, deny-by-default authorization, atomic state/event/outbox writes, and the no-arbitrary-execution rule are release-blocking invariants.

## License

Apache License 2.0. See [LICENSE](LICENSE).
