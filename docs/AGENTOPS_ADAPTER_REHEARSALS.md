# AgentOps candidate adapter rehearsals

Status: **PREPARED / REVERSIBLE / NO ALIAS ACTIVATION**.

`agentops adapter-rehearsal --input <file.json>` exercises the five sources whose migration contracts are `candidate_adapter`. The command is a proof boundary, not a legacy CLI alias and not a consumer migration mechanism.

Every request must name the exact reviewed source repository and exact source Git SHA. Every successful receipt states all of the following:

- `rehearsal_only=true`;
- `alias_activated=false`;
- `migration_performed=false`;
- `consumer_mutation_performed=false`;
- `source_retirement_authorized=false`.

A source SHA mismatch fails closed.

## agentmesh

The source contract only proves counts: all agents healthy and at least one route. Counts cannot establish identity, ownership, capabilities, or authorization. The adapter therefore requires an explicit target routing payload supplied by the caller, runs the target routing validator, and verifies that agent, healthy-agent, and active-route counts match the source observation.

No owner or capability is inferred from a repository name or count.

## context-window-budgeter

The source and target both prioritize required sections, but their optional tie-break differs. The source orders equal-priority sections by name; the target normally considers token size before identifier.

The adapter first computes the exact source order, then assigns unique adapter-only target priorities derived from that full order. This removes the target size tie-break from the compatibility path while retaining the target's bounded validation and accounting. The receipt compares included and excluded sections against the source-semantic plan.

The translated priority is compatibility metadata. It is not presented as the original source priority.

## agent-quota-simulator

The source uses `tokens`, `seconds`, and `cost_micros`. The target uses `tokens`, `time_ms`, and `micro_cost`, plus a required-task concept absent from the source.

The adapter performs only exact mappings:

- `tokens -> tokens`;
- `seconds -> time_ms` by integer multiplication by 1000;
- `cost_micros -> micro_cost` as an identity mapping;
- every translated task has `required=false`.

If an exact seconds-to-milliseconds conversion exceeds the target bound, the adapter blocks rather than truncating, saturating, or rounding. Source and target admitted/rejected sets and remaining budgets must match after unit conversion.

## agent-session-recorder

The source transcript has sequence, kind, and content but no target session identity, actor, or timestamp. The adapter requires those missing facts explicitly from `adapter_input` and creates deterministic `legacy-N` event identifiers.

Source content is nested unchanged under `data.legacy_content`. Target sensitive-key validation is still applied recursively; a source event containing a target-forbidden sensitive key fails closed instead of being silently redacted.

Source hash heads are not reused and source authenticity is never promoted into target authenticity. The receipt explicitly records `authenticity_transferred=false`.

## circuit-breaker-lab

The source silently transitions from open to half-open when wall-clock cooldown has elapsed. The target requires an explicit `cooldown_elapsed` event.

The adapter accepts only bounded, nondecreasing integer `at_ms` observations. While the source-equivalent state is open:

- if supplied time proves the cooldown elapsed, it emits an explicit target `cooldown_elapsed` event before the source outcome;
- if the cooldown has not elapsed, the attempted source call is recorded as suppressed and no fabricated target outcome is emitted.

The target uses `success_threshold=1`, matching the source behavior where one successful half-open attempt closes the circuit. Final source-equivalent and target states must match.

## What this unlocks

These adapters let compatibility work continue while the formal migration gate remains closed. They can be expanded with more fixtures and counter-proofs without changing any consumer or source repository.

They do **not** unlock package aliases, import aliases, legacy CLI aliases, redirects, source archival, release publication, or consumer mutation. Those actions remain gated by complete pilot/adopter evidence, reviewed default-branch live evidence, and named human approval.
