# SQLite schema

## `missions`

The current task and decision are stored as canonical JSON alongside queryable operational columns.

| Column | Meaning |
| --- | --- |
| `mission_id` | Stable mission identity |
| `idempotency_key` | Unique producer key preventing duplicates |
| `task_json` | Scope, criteria, permissions, budget, latency, context, and metadata |
| `decision_json` | Selected agent, score, rejected candidates, disagreement, and escalation |
| `state` | Explicit mission state |
| `owner` | Human or team owner inherited from the selected agent |
| `attempt` / `max_attempts` | Bounded retry accounting |
| `lease_owner` / `lease_until` | Current worker lease |
| `last_error` | Latest visible failure reason |
| `evidence_json` | Completion proof when present |
| `approval_required` / `approved` | Human gate state |
| `human_interventions` | Count of recorded approvals |
| `created_at` / `updated_at` | UTC wall-time evidence |

## `mission_events`

Events are append-only through the public store API. Each row records mission, source and target states, actor, reason, optional evidence, and UTC time. The autoincrement sequence provides a stable replay order.

SQLite uses foreign keys and WAL mode. Claim, transition, approval, recovery, retry, and event append happen inside `BEGIN IMMEDIATE` transactions.
