# AgentOps compatibility contract

## Current phase

`REHEARSAL`. The thirteen source repositories remain supported and unchanged. The target interfaces are opt-in. No existing distribution name, import path, CLI command, schema, redirect, release, or repository is replaced by this branch.

The exact source package/import/CLI inventory is executable with:

```bash
agentops compatibility
```

The command emits deterministic SHA-bound evidence and records `legacy_aliases_activated: false`, `migration_performed: false`, and `compatibility_gate: not_run`.

## Exact observed source interfaces

Every row below is bound to the source Git SHA and the `pyproject.toml` blob stored in `SOURCE_INTERFACES`. Values are copied from those observed source manifests; repository name, distribution name, import root, and CLI are intentionally not assumed to be identical.

| Repository | Distribution | Import root | Existing CLI | Prepared target surface |
| --- | --- | --- | --- | --- |
| `agentmesh` | `agentmesh` | `agentmesh` | `agentmesh` | `agentops route` |
| `agent-budgeter` | `agent-budgeter` | `agent_budgeter` | `agent-budgeter` | durable core budgets; no alias |
| `agent-inbox` | `agent-inbox` | `agent_inbox` | `agent-inbox` | `agentops inbox` |
| `agent-quota-simulator` | `agent-quota-simulator` | `agent_quota_simulator` | `agent-quota-simulator` | `agentops quota` |
| `agent-retry-kit` | `agent-retry-kit` | `agent_retry_kit` | `agent-retry-kit` | durable core retry policy; no alias |
| `agent-session-recorder` | `agent-session-recorder` | `agent_session_recorder` | `agent-session-recorder` | `agentops session-record` / `session-verify` |
| `circuit-breaker-lab` | `circuit-breaker-lab` | `circuit_breaker_lab` | `circuit-breaker-lab` | `agentops circuit` |
| `context-window-budgeter` | `context-window-budgeter` | `context_window_budgeter` | `context-budget` | `agentops context` |
| `human-in-the-loop-queue` | `human-in-the-loop-queue` | `human_in_the_loop_queue` | `human-in-the-loop-queue` | durable core approvals; no alias |
| `idempotency-kit` | `idempotency-kit` | `idempotency_kit` | `idempotency-kit` | durable core idempotency; no alias |
| `taskgraph` | `taskgraph-agents` | `taskgraph` | `taskgraph` | durable core DAG; no alias |
| `timeout-toolkit` | `timeout-toolkit` | `timeout_toolkit` | `timeout-toolkit` | durable core deadlines; no alias |
| `automation-control-plane` | `automation-control-plane` | `automation_control_plane` | `automation-control-plane` | existing CLI plus prepared `agentops` entry point |

Two important non-obvious identities are preserved as counter-proofs against guesswork: `taskgraph` publishes distribution `taskgraph-agents`, and `context-window-budgeter` exposes CLI `context-budget`.

## Deliberate non-aliases

No legacy source command is silently aliased yet. Existing source inputs and outputs have different strictness, persistence, and evidence semantics. Treating a similarly named target command as drop-in compatibility would create a false migration claim.

Before any legacy alias is activated, the exact source interface requires:

1. source SHA and target implementation/reimplementation SHA;
2. source positive fixture captured from the exact source interface;
3. source negative fixture proving its failure semantics;
4. target fixture proving compatible behavior or an explicitly versioned incompatibility;
5. package/import/CLI alias test where compatibility is promised;
6. live consumer inventory and migration plan;
7. final supported source release and deprecation window;
8. verified redirect documentation;
9. real rollback rehearsal bound to migration SHAs;
10. named human approval.

A missing fixture is `NOT_RUN`, not a pass. A source may remain standalone when equivalence cannot be demonstrated without weakening behavior.

## Supported rehearsal runtime

The current target CI contract explicitly covers CPython 3.11 and 3.12 on Ubuntu 24.04. Other Python versions and operating systems are not yet verified by this AgentOps rehearsal and must not be inferred as supported.

## Verification

```bash
agentops compatibility
python -m unittest tests.test_agentops_compatibility -v
```

The tests fail if source SHA membership drifts, a source identity is duplicated, import roots are guessed inconsistently with observed entry points, a legacy alias becomes active, or a non-obvious source identity such as `taskgraph-agents` / `context-budget` is normalized away.
