# Threat model

## Assets and trust boundaries

Protected assets are workflow definitions, authorization assignments, approvals, job/step state, budgets, handler results, audit history, and outbox delivery state.

Trusted components are the local process, reviewed handler registry, SQLite library/database permissions, caller-supplied authenticated principal mapping, and operating-system clock. Workflow JSON, job payloads, trigger requests, handler outputs, CLI arguments, and HTTP paths are untrusted inputs.

## Security properties

- Untrusted JSON cannot grant identity or capabilities.
- A workflow cannot introduce executable code.
- A step runs only when DAG prerequisites, approval, both trusted-registry and workflow capabilities, reserved budget, deadline, lease, and kill checks all pass.
- Concurrent workers cannot both accept the same ready step lease.
- A stale/crashed worker cannot commit a result after lease replacement or a job fence generation change.
- Approval applies to the exact workflow/input/step version checked at claim.
- State, audit, and outbox changes commit atomically.
- Mutating stored events, deleting a chain tail, or modifying a successful result is detectable against database-held anchors and result receipts.
- All parser sizes, attempts, costs, deadlines, leases, CLI errors, and HTTP pagination are bounded.
- Lease tokens are returned only by claim operations, never by read/list/dashboard surfaces.

## Abuse cases and mitigations

| Abuse case | Mitigation | Residual risk |
| --- | --- | --- |
| Workflow supplies code or weakens a handler capability | No shell/dynamic handler; trusted startup code binds each registry name to an authoritative capability, checked in addition to the workflow capability. | A maintainer could register an unsafe generic handler or bind it too broadly. |
| Duplicate webhook/schedule delivery | Unique `(workflow_id, idempotency_key)` inside the write transaction. | Callers must derive stable keys. |
| Two workers race | `BEGIN IMMEDIATE`, under-lock clock sample, state/version predicate, unique lease token, fence generation. | SQLite is single-node coordination. |
| Worker crashes after external side effect | Lease recovery and exactly-once acceptance. | Invocation is at least once; external side effects need their own idempotency key. |
| Approval replay after definition/input change | Workflow and input digests plus authorized step version checked at claim. | Caller identity authentication is external. |
| Submitter self-approves | Denied unless explicitly granted `approval:self`; admin wildcard is an override. | Poor role assignment can defeat separation of duty. |
| Budget bypass or concurrent oversubscription | Integer-only schema; claim reserves estimates atomically; failure releases and success settles actual cost. | Handler-reported cost must be trustworthy. |
| Operation completes after emergency stop | Enabling a kill switch immediately terminalizes matching jobs and increments their fence generation; later disable cannot validate the old lease. | In-process code already running cannot be forcibly terminated safely. |
| Audit/result row is edited or the tail is deleted | SHA-256 chain, stored count/head anchors, result digest, and success-event digest are recomputed together. | Full-database attacker can rewrite all state and anchors; checkpoint externally. |
| Backup path is a symlink or appears during publish | Exact symlink destinations are refused; private temporary copy plus no-overwrite hard-link publication is used. | Directory permissions and parent-path trust remain deployment responsibilities. |
| Remote dashboard mutation | Loopback-only bind, read-only routes, write methods rejected, security headers. | Local readers see stored payloads. |
| Resource exhaustion | Bounded JSON, DAG size, retries, text, pagination, and time values. | Many individually valid submissions can fill disk; deployment quotas are external. |

## Explicitly unsupported patterns

Do not register generic `shell`, `python`, `http`, `sql`, `template-eval`, dynamic module, or arbitrary filesystem handlers. Do not expose the CLI or read-only server as an unauthenticated remote service. Do not put credentials or private data in definitions, payloads, examples, logs, or audit reasons. Do not treat the local principal string as authentication.

## Security review checklist

- Does every mutation authorize inside the transaction using trusted stored roles?
- Does every state decision re-read current state and include a conflict/lease predicate?
- Are state, event, and outbox changes atomic?
- Does a new handler accept only a narrow typed action rather than a generic execution primitive?
- Are inputs/results bounded before storage?
- Are approvals synchronized and then strictly matched to current job/step versions?
- Are kill/deadline/budget checks and reservation/fence settlement preserved on both claim and completion paths?
- Do tests exercise negative, crash, concurrency, and tamper cases?
