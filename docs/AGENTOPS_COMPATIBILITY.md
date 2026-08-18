# AgentOps compatibility contract

## Current phase

The source repositories remain supported and unchanged. The new target commands are opt-in rehearsal interfaces. No existing package name, import path, CLI command, schema, or release is redirected by this change.

## Proposed command mapping

| Existing concept | Rehearsal target command | Compatibility state |
| --- | --- | --- |
| Agent route evidence | `python -m automation_control_plane.agentops route` | New opt-in contract |
| Context planning | `python -m automation_control_plane.agentops context` | New opt-in contract |
| Quota simulation | `python -m automation_control_plane.agentops quota` | New opt-in contract |
| Session record/verify | `python -m automation_control_plane.agentops session-record` / `session-verify` | New opt-in contract |
| Circuit simulation | `python -m automation_control_plane.agentops circuit` | New opt-in contract |
| Operator inbox projection | `python -m automation_control_plane.agentops inbox` | New opt-in contract |

## Deliberate non-aliases

No legacy command is silently aliased yet. Existing inputs differ in strictness and evidence semantics, and an automatic alias could convert malformed or weaker evidence into an apparent pass.

Before any deprecation, each source requires:

1. an exact source SHA and target import/reimplementation commit;
2. positive and negative compatibility fixtures;
3. package/import/CLI alias tests where a stable old interface exists;
4. a final supported source release and migration deadline;
5. consumer inventory and successful migration;
6. verified redirect documentation;
7. rehearsed rollback;
8. named human approval.

## Stability promise for the rehearsal

The `agentops.v1` receipt shape is experimental. It separates `passed`, `failed`, and `blocked`, uses integer-only planning fields, and includes deterministic SHA-256 evidence. A stable release requires a separate review and versioned compatibility decision.
