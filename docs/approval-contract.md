# Attempt approvals

An optional task field, `"approval": "required"`, blocks that task until a local
operator records a matching decision. Omit the field for historical behavior.
Omission preserves the exact canonical specification JSON and SHA-256; `null`,
booleans and other strings are rejected. This is an additive parser capability,
not an automatic approval requirement for existing specifications.

The native FactoryStore remains the only task authority. Schema version 2 introduced
approval decisions and approval wait/clock metadata in the same SQLite database.
The v1 migration is transactional and preserves existing run, task, receipt and
event rows. An old v1 binary cannot reopen v2 state; retain a verified v1 backup
before deployment if a binary downgrade is required. No migration is performed
on unknown schema versions. No old source repository or history is removed.

The current additive schema is version 3, which also stores optional native
[execution quotas](execution-quota-contract.md). Existing approvals remain in
the same Store; quota and approval waits share the active-wall clock.

## Review and decision

Use `approval-request --db DATABASE --run-id RUN --task-id TASK` to inspect a
ready task's next attempt. Pending dependencies and unfinished retry delays must
be resolved by the normal scheduler first. The JSON response binds the run,
specification SHA-256, task ID, next attempt, command arguments, declared environment,
tests, paths/artifacts, workspace and budgets. Its `request_sha256` covers the
complete response except that digest field itself.

Record the decision with `approval-decide`, using the same database/run/task and
the response's `--attempt` and `--request-sha256`. Required remaining arguments
are `--decision approved|rejected`, `--decided-by IDENTIFIER`,
`--expires-at EPOCH_SECONDS` and `--decision-id IDENTIFIER`.

Python callers use `FactoryStore.approval_request(run_id, task_id)` and
`FactoryStore.record_approval(run_id, task_id, attempt=..., request_sha256=...,
decision=..., decided_by=..., expires_at=..., decision_id=...)`.

| Property | Contract |
| --- | --- |
| Attempt | Integer 1–100, exactly the task's next available attempt |
| IDs and declared actor | 1–128 ASCII characters, first alphanumeric, then alphanumeric or `._:@/-` |
| Request digest | Exactly 64 lowercase hexadecimal characters |
| Decision | Exactly `approved` or `rejected` |
| Time | Finite numeric epoch seconds, never boolean; 0 through 253402300799 |
| Expiry | Strictly after the observed host time, at most 86400 seconds ahead |
| Clock | Observed under the SQLite write lock; a regression behind a persisted observation refuses authorization |
| Retention | At most 1000 decisions per run; reaching the limit refuses additions and preserves all records |
| Replay | The same decision ID and content is idempotent; changed content conflicts |

`decided_at` comes from the store clock, never the request document. A new explicit
decision ID can replace a rejected or expired decision while the attempt remains
ready; both decisions remain in the journal. An owned/running attempt cannot have
its decision changed through this API. A recovered attempt needs its own new
approval. Replaying an old decision never extends its expiry.

## Execution and recovery

The claim checks the current decision, exact request digest, journal linkage and
clock. The lease and each process timeout are capped by approval expiry. Publication
checks approval again inside the existing completion write transaction. If a
publication callback crosses expiry, its existing compensating action restores
the prior bytes and the receipt/transition transaction rolls back.

Protected tasks accept only the effective requests produced by the native
SpecProvider contract. A custom Provider that changes command, environment, working
directory, label, timeout or output cap is refused before that changed request is
executed; test requests receive the same check. The approved contract does not
authenticate executable binaries, ambient system libraries or workspace input
bytes. Specifications/executors remain trusted local input. This feature is not
an OS sandbox or an authorization service for untrusted remote callers.

When only unapproved ready work remains and no worker owns runnable work,
`FactoryEngine.run` returns promptly with `waiting_for_approval=True`.
The durable run state remains `running`; the snapshot's `execution_status` is
`waiting_approval` and `waiting_for_approval` lists each task, attempt and reason.
The CLI uses exit code 3 for this state, 0 for success, 2 for failure/refusal.
It is never a success receipt. Approval wait time is excluded from active wall
budget in persisted metadata; actual execution/retry waiting remains bounded.
Independent unprotected tasks remain runnable. A resumed worker reuses the same
run, task transitions, lease owner/attempt fencing and event chain.

Exports retain the historical task-receipt field set. Approval decisions live
in the native hash-chained events and are included in the export. `verify_export`
requires a valid decision preceding each protected claim, the exact request
digest, and times covering execution through accepted publication. It does not
convert the declared actor into an authenticated person.

## Trust and remaining scope

Every decision explicitly reports `actor_authentication: not_established`.
The host clock, local API caller, SQLite database and executor are trusted boundaries.
Database-held hashes detect inconsistent edits; they are not independent signatures
against a complete database rewrite. Simulated clocks and fixtures are test evidence,
never human approval or deployment evidence. Direct SQL edits are unsupported.

No second inbox, agent registry, role authority, session recorder, external outbox
or manual handoff queue is introduced. Capability/role authentication and a remote
approval UI remain outside this tranche. Native crash recovery already fences old
owners; acceptance exercises two processes, durable reopen, interrupted migration,
wrong/expired decisions, clock regression, changed Providers and byte rollback.
