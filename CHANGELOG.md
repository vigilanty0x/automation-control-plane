# Changelog

All notable changes are documented here. The project follows semantic versioning for its Python and workflow-schema contracts.

## 1.0.0 - 2026-08-16

### Added

- Strict versioned workflow/DAG schema `1.0` with canonical digests and cycle validation.
- SQLite persistence for immutable workflows, jobs, step runs, approvals, roles, kill switches, audit events, and transactional outbox records.
- Idempotent submission, optimistic operator versions, atomic leases, retries/backoff, deadlines, budgets, dry-run execution, crash recovery, and reconciliation.
- Deny-by-default RBAC with separate operator, approver, worker, dispatcher, and viewer roles.
- Workflow/input/step-version-bound approvals and submitter/approver separation of duty.
- Global and per-workflow versioned kill switches.
- Explicit safe-handler registry and four side-effect-free built-ins.
- Hash-chained audit verification, online backup, validated atomic restore, machine-readable operational CLI, and loopback-only read-only dashboard/API.
- Bounded JSON ingestion rejects duplicate object members instead of applying ambiguous last-key-wins parsing.
- Synthetic flagship DAG and broad tests for persistence, concurrency, crashes, leases, retries, idempotency, approval, kills, budget, audit, CLI, backup, and HTTP boundaries.
- Database schema `2` adds atomic budget reservations, per-job fence generations, lease fencing, and canonical successful-result receipts, with transactional migration from schema `1`.
- Handler capabilities are bound by the trusted registry and checked in addition to workflow-declared capabilities, preventing workflow-controlled confused-deputy downgrades.
- Terminal job transitions immediately close active steps and reservations; kill epochs remain effective after a switch is disabled, and recovery never revives terminal work.
- Approval records are strictly checked against the current workflow/input/job/step versions while job state remains derived from the whole DAG.
- Audit verification now anchors event count/head and binds result JSON to its success event; backup/restore refuses exact symlinks and validates the complete schema and receipts before atomic publication.
- Claims scan the complete eligible queue without the former 100-row starvation window, and time-sensitive validation samples the clock after acquiring the write lock.

### Compatibility

- Preserved the original `core.run`, `core.simulate`, and `core.transition` behavior.
- Preserved legacy `automation-control-plane [JSON_PATH]` simulation invocation.

## 0.1.0 - 2026-08-15

- Initial stateless governed transition and simulation boundary.
