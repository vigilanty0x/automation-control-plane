# AgentOps consolidation contract

`automation-control-plane` is the canonical repository selected for the AgentOps product entity. `agentops` is the canonical public product/import/CLI identity introduced as a compatibility layer; the existing `automation_control_plane` package and `automation-control-plane` CLI remain supported during migration.

## Source families to absorb

The consolidation plan assigns AgentOps responsibilities currently split across agent orchestration repositories such as agentmesh, agent-dashboard, agent-handoff, agent-inbox, agent-budgeter, agent-retry-kit, agent-session-recorder, agent-worktrees, context-window-budgeter, agent-quota-simulator and related orchestration/reliability satellites.

Absorption is module-by-module. A source is not archived merely because an equivalent feature exists in the target.

## Canonical boundaries

The durable core remains `automation_control_plane` and currently owns workflow validation, SQLite persistence, DAG execution, approvals, leases, budgets, retries, deadlines, kill switches, recovery, outbox and audit.

New AgentOps-facing integrations may use:

```python
import agentops
from agentops import ControlPlane, ControlPlaneStore
```

and:

```bash
agentops --help
python -m agentops --help
```

Legacy consumers continue to use `automation_control_plane`, `automation-control-plane`, and `python -m automation_control_plane.cli` during the compatibility window.

## Consumer inventory

Before deprecating any source repository, record every public consumer of:

- `automation_control_plane`
- `automation-control-plane`
- `agentops`
- source package/import names for AgentOps satellites
- source CLI names
- workflow schema copies or JSON contracts
- GitHub Actions/workflows invoking a source repository

Each consumer must record repository, path, ref/SHA, surface type, target replacement and state (`MIGRATED`, `LEGACY_SUPPORTED`, `NO_CHANGE_REQUIRED`, `BLOCKED`).

## Compatibility rules

1. Existing imports must not silently change semantics.
2. Existing CLI exit codes and machine-readable JSON contracts remain stable unless versioned explicitly.
3. Existing durable SQLite databases are never rewritten without a schema migration and recovery test.
4. A source feature enters AgentOps only behind an explicit module boundary and tests.
5. The target must preserve fail-closed behavior for approvals, capability checks, leases, budgets and kill switches.

## Rollback

The migration is additive. If the AgentOps alias or a newly absorbed module fails:

1. pin consumers to the last verified `automation-control-plane` release/commit;
2. use `automation_control_plane` and `automation-control-plane` directly;
3. disable only the newly introduced module/alias, not the durable database;
4. restore a database only from a validated pre-migration backup when a schema migration is involved;
5. leave source repositories public and unarchived until the rollback window closes.

Rollback acceptance requires legacy import/CLI smoke tests, database integrity/audit verification, and at least one recovered in-flight job/lease scenario when persistence changed.

## Archive gate

A source repository becomes an archive candidate only after target release, consumer migration, redirects/deprecation notes, rollback rehearsal and explicit human approval. This document does not authorize archive, deletion, merge, release or PR closure.
