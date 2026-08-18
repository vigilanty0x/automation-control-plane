# AgentOps public consumer preflight — 2026-08-18

Status: **PRE-FLIGHT COMPLETE; migration gate remains closed**.

This note records the read-only branch-run evidence from GitHub Actions run `32160869310` at head `7e0185f258d9d775086af92a5787a0ed3b942da3`. It is discovery evidence only and is not a migration, redirect, release, rollback, archive, or alias-activation authorization.

## Observed public portfolio

The collector enumerated and scanned all **112 of 112 public repositories** returned for the owner. No source SHA drift was detected across the thirteen SHA-bound AgentOps sources.

The scan produced:

- 78 total references;
- 16 unique consumer repositories;
- 61 documentation references;
- 17 references classified by the current scanner as `import` because they occur in code files;
- 0 package references;
- 0 workflow references;
- 0 fork references;
- 0 explicit pilot references.

The inventory file digest was `6a1939592ea804094dad33051ec032b26e7daf07d51903386bdd48f6718544d9` and the receipt file digest was `bec12a6c56dd15de6d20a9f8a70a4e5c0027193823714114ea23cac29e8ec7a3`.

The uploaded workflow artifact is `9333575734` with ZIP digest `sha256:53e1f8d84b65c883c37ebcc4fe0ada426f0b178afa933f1f29e09632945fce4b` and retention through 2026-09-17.

## Satellite-source result

For each of the twelve satellite repositories — `agentmesh`, `agent-budgeter`, `agent-inbox`, `agent-quota-simulator`, `agent-retry-kit`, `agent-session-recorder`, `circuit-breaker-lab`, `context-window-budgeter`, `human-in-the-loop-queue`, `idempotency-kit`, `taskgraph`, and `timeout-toolkit` — the public scan found only documentation references in the portfolio `.github` repository.

No satellite source had a detected package, workflow, fork, or code-file reference in the 112-repository public portfolio snapshot.

This materially reduces the public migration surface but does **not** prove that private, external, unpublished, or human-known pilots do not exist.

## Core-source result

`automation-control-plane` produced 40 references: 23 documentation references and 17 references classified as `import` by the current scanner. Those 17 references span `.github` plus fifteen public utility repositories.

The classification is intentionally treated as coarse. The current archive scanner classifies a source-name match in any code file as `import`; it does not require Python/JavaScript import syntax. A sampled reference in `audit-trail-lite/scripts/check.py` is a membership entry in the `SIBLING_MODULES` checker set rather than an actual runtime import statement. Therefore the 17 count must be interpreted as **code-file references requiring line-level triage**, not as 17 proven runtime imports.

No migration decision may use the coarse `import` label as runtime-dependency proof without validating the referenced line.

## Why the receipt is failed

The structural public scan completed, but pilot coverage was not explicitly declared complete. The consumer contract intentionally refuses to convert an absent pilot manifest into a claim of zero pilots.

The receipt therefore remains `failed`, with `coverage_complete=false`, `mutation_performed=false`, and `portfolio_gate=not_run`.

This is the expected fail-closed result.

## What can safely proceed now

The twelve versioned migration contracts can be used to prepare adapters, evidence-only bridges, or incompatibility boundaries because the public static consumer surface is now observed. Alias activation and source retirement remain blocked.

The remaining human-information gap is narrow and explicit: pilot/adopter completeness. Use `docs/AGENTOPS_PILOT_MANIFEST_TEMPLATE.json` to record that evidence. `complete` must remain `false` until a human has actually checked whether the source set has any known pilots or adopters outside the public static scan.

After a complete pilot manifest exists, rerun the manual `AgentOps public consumer evidence` workflow from a reviewed default-branch lineage. Only that later run can be considered for the formal migration gate.
