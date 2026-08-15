# SQLite schema

## `missions`

Each idempotency key maps to one immutable mission identity, branch, and worktree path.

| Field | Meaning |
| --- | --- |
| `request_json` | Agent, owner, base ref, ownership, criteria, retries, and metadata |
| `fingerprint` | Canonical request, repository, and worktree-root digest |
| `state` | Explicit mission state |
| `branch` / `worktree_path` | Stable Git resources for the mission |
| `attempt` / `max_attempts` | Bounded retry accounting |
| `last_error` | Latest visible failure or rejection reason |
| `evidence_json` | Completion proof accepted by the gates |
| `cleaned_at` | Time at which integrated resources were safely released |
| `human_interventions` | Explicit recorded human actions |
| `created_at` / `updated_at` | UTC wall-time evidence |

SQLite enforces unique idempotency keys, branch names, and worktree paths. Writes use `BEGIN IMMEDIATE`, foreign keys, WAL mode, and a bounded busy timeout.

## `mission_events`

The append-only public API records ordered source and target states, actor, reason, optional evidence, optional structured details, and UTC time. Cleanup and intervention events may keep the same state while recording a new operational fact.
