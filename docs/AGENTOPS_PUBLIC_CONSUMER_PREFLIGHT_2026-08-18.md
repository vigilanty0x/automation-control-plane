# AgentOps public consumer preflight — 2026-08-18

Status: **PRE-FLIGHT COMPLETE; migration gate remains closed**.

This note records the read-only branch-run evidence from GitHub Actions run `32162128261` at head `6c03a279bd16097939cdf3c2c83d69dee981bf46`. It is discovery evidence only and is not a migration, redirect, release, rollback, archive, or alias-activation authorization.

## Observed public portfolio

The collector enumerated and scanned all **112 of 112 public repositories** returned for the owner. No source SHA drift was detected across the thirteen SHA-bound AgentOps sources.

The scan produced:

- 82 total references;
- 18 unique consumer repositories;
- 63 documentation references;
- 19 references provisionally classified by the archive scanner as `import` because they occur in code files;
- 0 package references;
- 0 workflow references;
- 0 fork references;
- 0 explicit pilot references.

The inventory file digest is `51f50c86d942e6999017ddaf8b690c2273f2d5ffe7ed0dfd1d915cec0211f937`. The receipt file digest is `41e5e43c9e675c8f2ba2b7663bb2ee5176291a27df5b36cb1f933347cca6351a`, and the consumer receipt evidence SHA-256 is `d203b805a0dc8dcf39ae965730f6bafc5cbae4521d21e3298f43ff8052198c3b`.

The uploaded workflow artifact is `9334038317` with ZIP digest `sha256:d7f3c77ae07887a110e46f22890ab5ff74bba86483fea8863661f68833a88c5e` and retention through 2026-09-17.

## Syntax-backed code-reference triage

The preflight now runs a second, independent read-only pass over every provisional `import` reference. It resolves the exact SHA-bound `github://` line, fetches the referenced file at that immutable commit, and requires recognizable language import/dependency syntax before treating the match as runtime import evidence.

Result for all 19 provisional candidates:

- **0 verified runtime imports**;
- **19 code mentions**;
- **0 unresolved references**;
- triage status: `passed`;
- triage evidence SHA-256: `ee5379701b54f85c8a9dd312a1fe14273b57746c74db3b226b3cdafd4d36db15`;
- `mutation_performed=false`;
- `migration_authorized=false`.

This removes the false impression that the current public portfolio contains 19 proven runtime consumers of `automation-control-plane`. It does not delete or rewrite the original coarse evidence: the raw scanner receipt remains intact, and the triage receipt records why each candidate is downgraded to a code mention.

## Satellite-source result

For each of the twelve satellite repositories — `agentmesh`, `agent-budgeter`, `agent-inbox`, `agent-quota-simulator`, `agent-retry-kit`, `agent-session-recorder`, `circuit-breaker-lab`, `context-window-budgeter`, `human-in-the-loop-queue`, `idempotency-kit`, `taskgraph`, and `timeout-toolkit` — the public scan found only documentation references in the portfolio `.github` repository.

No satellite source had a detected package, workflow, fork, provisional code-file reference, or verified runtime import in the 112-repository public portfolio snapshot.

This materially reduces the observed public migration surface but does **not** prove that private, external, unpublished, or human-known pilots do not exist.

## Core-source result

`automation-control-plane` is the only source with provisional code-file references. The 19 candidates occur in portfolio/checker code such as sibling-module registries and architecture name lists, including `.github`, utility repositories, `portfolio-kit`, `proofgate`, and `workflow-templates`.

The syntax-backed triage classified every one of those 19 candidates as `code_mention`, not `verified_import`. Therefore the current public evidence shows **zero verified runtime imports of any of the thirteen AgentOps sources** across the 112 scanned repositories.

That statement is deliberately limited to the public snapshot and the syntax rules covered by the triage tool. It is not a claim about private repositories, external users, local installations, unpublished consumers, or human-known pilots.

## Why the structural receipt is still failed

The public static scan completed, source SHA drift is zero, and all provisional import candidates were resolved. Pilot coverage, however, was not explicitly declared complete. The consumer contract intentionally refuses to convert an absent pilot manifest into a claim of zero pilots.

The structural receipt therefore remains `failed`, with `coverage_complete=false`, `mutation_performed=false`, and `portfolio_gate=not_run`.

This is the expected fail-closed result and is now the only unresolved evidence class in this preflight.

## What can safely proceed now

The twelve versioned migration contracts can be used to prepare adapters, evidence-only bridges, incompatibility boundaries, and a deterministic migration plan because the observable public static consumer surface is now both scanned and line-triaged. Alias activation and source retirement remain blocked.

The remaining human-information gap is narrow and explicit: pilot/adopter completeness. Use `docs/AGENTOPS_PILOT_MANIFEST_TEMPLATE.json` to record that evidence. `complete` must remain `false` until a human has actually checked whether the source set has any known pilots or adopters outside the public static scan.

After a complete pilot manifest exists, rerun the manual `AgentOps public consumer evidence` workflow from a reviewed default-branch lineage. Only that later run can be considered for the formal migration gate.
