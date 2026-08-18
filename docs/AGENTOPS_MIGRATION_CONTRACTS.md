# AgentOps migration contracts

Status: **PREPARED — no alias or migration activated**.

This document turns the twelve SHA-bound compatibility counter-proofs into versioned planning contracts. The contracts narrow what a future adapter is allowed to claim. They are not implementations of legacy aliases, not migration receipts, and not authorization to retire source repositories.

The machine-readable inventory is emitted by:

```text
agentops migration-contracts
```

Every contract is bound to the exact source Git SHA already present in the compatibility inventory. A source SHA drift invalidates the exact-source match and requires the counter-proof to be rerun before the contract can be revised.

## Contract rules

Each satellite source must have exactly one contract. Every contract keeps `activation_state=blocked` until the consumer inventory and the named human approval gates pass. No contract may make an irreversible action available. Missing semantic information must fail closed rather than be guessed.

The allowed planning strategies are:

- `candidate_adapter`: a translation may be possible, but only if the documented semantic delta is explicitly handled and proven;
- `projection_only`: the target may expose a read-only view, but must not claim mutation or durability equivalence;
- `evidence_only`: legacy receipts may be retained as evidence, but cannot authorize or enforce target behavior;
- `incompatibility_contract`: the source and target contracts are not losslessly equivalent and no drop-in alias is permitted.

## Per-source decisions

### agentmesh

Candidate adapter only. A healthy eligible route may remain eligible, but target owner/capability identity must be supplied explicitly. Repository names or counts cannot be promoted into authorization identity.

### context-window-budgeter

Candidate adapter only. Required-first planning and required-overflow fail-closed behavior are preserved. Equal-priority optional tie-break behavior differs, so a future adapter must preserve source ordering explicitly or expose a versioned ordering choice.

### agent-quota-simulator

Candidate adapter only. Resource units and field names differ and the target adds required-task semantics. Unit conversion must be exact and bounded; target-only required-task behavior cannot be described as source behavior.

### agent-session-recorder

Candidate adapter only. Tamper detection and sequence validation may be preserved, but the target rejects sensitive keys and binds richer session evidence. Source receipts cannot be relabeled as target authenticity proofs.

### circuit-breaker-lab

Candidate adapter only. The threshold-to-open invariant is preserved. Source wall-clock cooldown and target explicit `cooldown_elapsed` events are different contracts; elapsed time must never be fabricated by an adapter.

### agent-inbox

Projection only. The source is a durable mutating SQLite queue while `agentops inbox` is intentionally read-only. Queue mutation remains with the durable control plane or the source until a separate durable migration is reviewed.

### agent-budgeter

Incompatibility contract. The source `calls/time_ms/tokens` vector is not losslessly equivalent to durable `budget_units` plus separate timeout/retry controls. No drop-in alias is allowed.

### agent-retry-kit

Incompatibility contract. The source owns retryable-error taxonomy and millisecond backoff. The durable target owns integer-second scheduling without that taxonomy. Any future adapter must version taxonomy mapping and delay quantization explicitly.

### human-in-the-loop-queue

Evidence only. A legacy approved/rejected queue record lacks the target job-version/action/principal/capability binding. Legacy records may be retained as historical evidence but can never authorize a durable transition without a new target-bound approval.

### idempotency-kit

Evidence only. The legacy fingerprint rule is not the durable target's complete canonical-request binding. Legacy receipts may be retained, but target idempotency decisions must be recomputed from the target request.

### taskgraph

Incompatibility contract. The source owns path scopes and required-evidence kinds; the durable target owns handler/capability/approval execution semantics and has no equivalent path-ownership field. Parsing the dependency DAG is not equivalence proof.

### timeout-toolkit

Evidence only. External `elapsed_ms` versus `timeout_ms` evidence is not durable deadline/lease/recovery enforcement. Legacy receipts may be attached as evidence but cannot be converted into target enforcement claims.

## Gates after this document

This work intentionally moves the project forward without bypassing the safety gates. The remaining sequence is:

1. execute a live public consumer inventory from a reviewed default-branch workflow lineage;
2. bind every detected consumer to the exact source/interface contract it uses;
3. select migration order only for sources whose consumer evidence is complete;
4. implement versioned adapters only where a `candidate_adapter` contract exists and fixtures prove the translation;
5. require named human approval before any alias activation, consumer migration, redirect, source archive, tag, or release.

Until those gates pass, the source repositories remain supported and every migration contract remains blocked.
