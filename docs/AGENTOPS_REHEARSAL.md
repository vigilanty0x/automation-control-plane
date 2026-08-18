# AgentOps contract rehearsal

## Status

`REHEARSAL` — not a release, not an archive decision, and not evidence that the standalone source repositories have been migrated.

The selected base is `automation-control-plane`. The rehearsal is intentionally stacked on the reviewed build-policy branch so the package metadata can declare an audited build-backend range while CI resolves an exact toolchain.

## Why this base

The base already owns the controls that would be unsafe to duplicate:

- durable SQLite jobs and DAG dependencies;
- idempotent submissions;
- bounded retries, deadlines, budgets, and kill switches;
- digest-bound approvals and atomic worker leases;
- recovery, outbox repair, and tamper-evident audit verification;
- a machine-readable CLI and loopback-only read-only dashboard;
- deny-by-default handler registration with no arbitrary shell, subprocess, network, or dynamic-import workflow handler.

The AgentOps rehearsal therefore adds only bounded planning, simulation, evidence, read-only projection, migration-evidence, and rollback-planning seams.

## Implemented modules

| Module | Contract | Mutation |
| --- | --- | --- |
| `routing_evidence` | Strict agents, routes, health, capability, and target-owner evidence | None |
| `context_budgets` | Required-first context planning with reserved output capacity | None |
| `quota_simulation` | Deterministic admission against integer token, time, and micro-cost budgets | None |
| `session_evidence` | Redaction-aware SHA-256 session chains and expected-head verification | None |
| `circuit_breakers` | Closed/open/half-open transition simulation with invalid-transition counter-proofs | None |
| `operator_inbox` | Read-only prioritization of exported job snapshots | None |
| `consumer_inventory_contract` | Fail-closed completeness gate for imports, packages, workflows, docs, forks, and pilot references | None |
| `rollback_contract` | Deterministic recovery ordering and negative checks for migration rollback evidence | None |

Existing core behavior remains authoritative for approvals, budgets, idempotency, retry policy, task graphs, and deadlines. No second durable queue or execution engine is introduced.

## CLI

Run from a source checkout:

```bash
PYTHONPATH=src python -m automation_control_plane.agentops inventory
PYTHONPATH=src python -m automation_control_plane.agentops route --input examples/agentops/routing.json
PYTHONPATH=src python -m automation_control_plane.agentops context --input examples/agentops/context.json
PYTHONPATH=src python -m automation_control_plane.agentops quota --input examples/agentops/quota.json
PYTHONPATH=src python -m automation_control_plane.agentops session-record --input examples/agentops/session.json
PYTHONPATH=src python -m automation_control_plane.agentops circuit --input examples/agentops/circuit.json
PYTHONPATH=src python -m automation_control_plane.agentops inbox --input examples/agentops/inbox.json
PYTHONPATH=src python -m automation_control_plane.agentops consumers --input examples/agentops/consumers.json
PYTHONPATH=src python -m automation_control_plane.agentops rollback --input examples/agentops/rollback.json
```

Every command emits one deterministic JSON object. Exit `0` means the supplied bounded contract passed. Exit `2` means a valid counterexample failed the contract or malformed input was blocked.

## Evidence semantics

- `passed`: the bounded contract evaluated successfully;
- `failed`: structurally valid input demonstrated a contract failure;
- `blocked`: input was malformed, ambiguous, duplicated, out of bounds, or unsafe;
- `evidence_sha256`: digest of the complete result before the digest field is added;
- no timestamp is injected into runtime evidence, so the same input produces the same receipt.

Source observations are separately dated and expire. See `AGENTOPS_SOURCE_INVENTORY.json`.

A passing consumer contract is not a claim that a live consumer scan occurred. A passing rollback contract is not a claim that a repository, package, redirect, or consumer was actually rolled back. Both receipts explicitly retain `portfolio_gate: not_run` until live evidence is bound to current SHAs and reviewed.

## Security boundaries

- JSON input is capped at 1 MB and output at 2 MB.
- Duplicate JSON members and floating-point values fail closed.
- Integers reject booleans and use explicit bounds.
- Session events reject sensitive key names such as credentials, authorization data, cookies, and private keys.
- Session integrity is distinct from authenticity. Authenticity is `verified` only when both a trusted initial digest and trusted expected head are supplied.
- The quota, circuit, consumer, and rollback modules do not execute external operations.
- The inbox module is a read-only projection and performs no job mutation.
- No module performs network access, shell execution, subprocess creation, dynamic import, or filesystem mutation beyond CLI input reads.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check.py
python -m build --no-isolation
```

The dedicated suite includes positive tests and counter-proofs for unhealthy routes, ownership mismatch, required-budget overflow, duplicate tasks, session tampering, sensitive event keys, invalid circuit transitions, terminal inbox filtering, duplicate JSON members, incomplete consumer coverage, duplicate consumer evidence, impossible consumer migration counts, failed rollback checks, and failure exit codes.

CI also builds sdist and wheel, installs both in separate clean environments, and verifies output parity for the durable CLI plus AgentOps inventory, consumer, and rollback commands.

## Gates still blocked

- named human approval of the canonical AgentOps product boundary;
- source-history import and exact migration commits;
- live consumer inventory and consumer migration;
- compatibility aliases for standalone commands;
- target release and package publication;
- redirects and deprecation windows;
- real rollback rehearsal bound to migration SHAs;
- source archive.

The consumer-inventory and rollback **contracts** are prepared and tested; the corresponding portfolio migration gates remain `NOT_RUN` or `BLOCKED`. No standalone source repository may be archived merely because this rehearsal passes.
