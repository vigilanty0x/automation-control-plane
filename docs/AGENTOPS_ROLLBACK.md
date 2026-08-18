# AgentOps rollback rehearsal contract

Rollback is a release and migration gate, not a sentence in documentation. This rehearsal adds a deterministic planner that proves the rollback contract can fail closed before any source is redirected or archived.

## What the command proves

`agentops rollback` validates:

- an immutable baseline target Git SHA;
- a distinct candidate Git SHA and explicit target state;
- whether redirects or compatibility aliases are active;
- the number of migrated consumers versus the known total;
- reachability of the baseline;
- restorability of source support;
- rehearsal of target disablement, redirect reversal, alias reversal, and consumer recovery.

A valid rehearsal emits the ordered recovery steps required by the supplied state. Missing recovery checks return `failed`. Invalid SHAs, impossible consumer counts, unknown states, duplicate JSON members, floats, and unexpected fields return `blocked`.

## Deliberate safety boundary

The function has no repository, filesystem, network, shell, package-manager, redirect, or release mutation path. Every result contains:

- `mutation_performed: false`;
- `rehearsal_only: true`;
- `portfolio_gate: not_run`.

A `passed` result therefore proves the deterministic rollback contract and counter-proof only. It does **not** claim that a real source repository, package alias, consumer, release, or redirect has been rolled back.

## Real gate

The portfolio rollback gate remains blocked until a reviewer can reproduce an actual rehearsal bound to the current migration SHAs and evidence. Source archive remains forbidden until release, compatibility, consumers, redirect, rollback, and named human approval all pass.
