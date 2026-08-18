# AgentOps compatibility contract

## Current phase

The source repositories remain supported and unchanged. The new target commands are opt-in rehearsal interfaces. No existing package name, import path, CLI command, schema, or release is redirected by this change.

The installed target entry point is `agentops`. The equivalent module form, `python -m automation_control_plane.agentops`, remains available for environments that deliberately avoid console scripts. Both forms call the same bounded parser and handlers.

## Proposed command mapping

| Existing concept | Rehearsal target command | Compatibility state |
| --- | --- | --- |
| Agent route evidence | `agentops route` | New opt-in contract |
| Context planning | `agentops context` | New opt-in contract |
| Quota simulation | `agentops quota` | New opt-in contract |
| Session record/verify | `agentops session-record` / `agentops session-verify` | New opt-in contract |
| Circuit simulation | `agentops circuit` | New opt-in contract |
| Operator inbox projection | `agentops inbox` | New opt-in contract |
| Exact source/disposition inventory | `agentops inventory` | New opt-in contract |

## Deliberate non-aliases

No legacy source command is silently aliased yet. The `agentops` entry point is the new target interface, not a claim that an existing package or command has already migrated. Existing inputs differ in strictness and evidence semantics, and an automatic legacy alias could convert malformed or weaker evidence into an apparent pass.

Before any deprecation, each source requires:

1. an exact source SHA and target import/reimplementation commit;
2. positive and negative compatibility fixtures;
3. package/import/CLI alias tests where a stable old interface exists;
4. a final supported source release and migration deadline;
5. consumer inventory and successful migration;
6. verified redirect documentation;
7. rehearsed rollback;
8. named human approval.

## Supported rehearsal runtime

The current CI contract explicitly covers CPython 3.11 and 3.12 on Ubuntu 24.04. Other Python versions and operating systems are **not yet verified** by this rehearsal and must not be inferred as supported.

## Stability promise for the rehearsal

The `agentops.v1` receipt shape is experimental. It separates `passed`, `failed`, and `blocked`, uses integer-only planning fields, and includes deterministic SHA-256 evidence. A stable release requires a separate review and versioned compatibility decision.
