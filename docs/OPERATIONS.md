# Operations and recovery

## Baseline runbook

1. Initialize the database on a local filesystem with `init`.
2. Protect the database and backup files as administrator-equivalent state. Initialization attempts mode `0600`.
3. Assign separate operator, worker, approver, dispatcher, and viewer principals when duties are separated.
4. Register reviewed immutable workflow versions.
5. Run workers under dedicated local identities with only required handler capabilities.
6. Run `reconcile` after a crash and periodically during operation.
7. Run `audit` and checkpoint the returned `events` count and `head_hash` independently.
8. Create and test backups on a schedule appropriate to the recovery point objective.

## Crash recovery

Worker crashes do not require manual row edits. `reconcile` first recovers every expired step lease:

- if attempts and job deadline permit, the step returns to `ready` after policy backoff;
- otherwise it becomes `failed` (or `cancelled` under a kill switch);
- the old owner/token can no longer complete;
- job state and downstream dependencies are recomputed;
- expired outbox leases return to `pending`.

Workers also perform lease recovery before a claim, so a restarted fleet naturally resumes.

## Kill-switch incident procedure

Enable globally:

```bash
automation-control-plane kill --db control-plane.db global --enable --reason "Incident reference"
```

Or stop one workflow:

```bash
automation-control-plane kill --db control-plane.db workflow WORKFLOW_ID --enable --reason "Incident reference"
```

Enabling the switch immediately marks every matching nonterminal job cancelled, clears its active leases and reservations, and advances its fence generation in the same transaction as the switch event. A handler already running in-process cannot be force-killed, but its lease can no longer publish a result. Disabling the switch permits new submissions and claims; it never revives jobs or lease tokens from the earlier kill epoch. Disable using the current kill-switch version when coordinating multiple operators:

```bash
automation-control-plane kill --db control-plane.db workflow WORKFLOW_ID --disable \
  --reason "Incident resolved" --version CURRENT_VERSION
```

## Backup and restore

Create an online, consistent backup:

```bash
automation-control-plane backup --db control-plane.db backups/control-plane-YYYYMMDD.db
```

The destination must not exist and must not be a symbolic link (including a dangling one). The command first verifies audit anchors and successful-result receipts, writes a private temporary SQLite backup, then revalidates its schema, integrity, chain, and receipts before publishing the exact destination atomically without overwrite. Verify a restore to a separate path before relying on it:

```bash
automation-control-plane restore --db restore-check.db backups/control-plane-YYYYMMDD.db
automation-control-plane audit --db restore-check.db
automation-control-plane list --db restore-check.db jobs
```

Restore requires SQLite integrity, the exact strict application tables/columns/index semantics, no unexpected trigger or view, valid foreign keys, audit count/head anchors, and valid successful-result receipts. A schema-version `1` source is migrated transactionally to database schema `2`; future, unknown, or incomplete schemas are refused. `--force` atomically replaces the exact destination only after validating a private temporary database. Source and destination symlinks are refused, as are destinations with WAL/SHM sidecars. Stop and checkpoint writers first when replacing an active database, retain the previous copy through the rollback window, and never use a broad/glob destination.

## Outbox delivery

State and its event/outbox envelope commit together. A trusted publisher can call `claim_outbox` and `acknowledge_outbox` under a principal with the `dispatcher` role. Delivery is at least once; consumers should deduplicate by `event_id` or `event_sequence`. Reconciliation recreates any missing outbox row from the canonical event, primarily as corruption/repair defense.

## Observability

The read-only API/dashboard is suitable for loopback inspection, not public exposure:

```bash
automation-control-plane serve --db control-plane.db --host 127.0.0.1 --port 8787
```

Monitor job counts by state, oldest ready step, lease expiry, retry frequency, budget exhaustion, deadline failures, kill-switch events, audit validity, and pending outbox age. This release intentionally avoids a network metrics dependency.

## SQLite operational notes

- Keep the database on a local filesystem. Network filesystem locking semantics may not meet SQLite requirements.
- Do not copy only the main file while writers run; use `backup`.
- WAL and `synchronous=FULL` favor integrity over maximum throughput.
- Writer serialization is expected. A persistent busy timeout failure is an operational signal, not a reason to bypass transactions.
- Direct SQL modification invalidates application invariants and may invalidate the audit chain.
- `init` performs a read-only schema-version preflight. It will not create application tables in an unknown or future-version database.
