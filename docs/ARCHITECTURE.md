# Architecture

## Flow

1. `CapabilityRegistry` validates and owns immutable `AgentProfile` records.
2. `ModelRouter` applies hard boundaries, scores only eligible agents, and returns a `RouteDecision` containing selection, rejection, disagreement, cost, and escalation evidence.
3. `ModelRouterService` passes that exact decision to `SQLiteMissionStore`.
4. The store creates one mission per idempotency key and appends an initial event.
5. A worker atomically claims a queued, approved mission with a bounded lease.
6. Every transition is checked by the explicit state machine and appended to the event trail.
7. `done` requires a complete `EvidenceBundle` covering the task's declared acceptance criteria.

## Invariants

- A routable mission has a selected agent and owner before it enters the queue.
- A rejected mission is persisted with candidate-specific reasons.
- An idempotency key cannot silently point to a different task payload.
- Only one transaction can claim a queued mission.
- A high-risk mission cannot be claimed before approval.
- Retry never creates a new mission ID.
- An expired lease becomes a visible failure event before retry.
- Terminal states cannot move.
- `done` cannot be inferred from agent text; it is a validated state transition with proof.

## Failure and rollback

The package never contacts an external model. Operational rollback is therefore data-local:

- stop workers;
- copy the SQLite file and its `-wal`/`-shm` companions if present;
- inspect `mission_events` to identify the last accepted state;
- restore the backup or enqueue a new task with a new idempotency key when the intended work truly changed.

Do not edit event rows to manufacture success. A correction should be a new visible event or a new mission.
