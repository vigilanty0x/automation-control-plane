# Acceptance matrix — 0.1.0

| # | Criterion | Evidence |
|---:|---|---|
| 1 | Local-first, no runtime cloud/dependency | capabilities, package metadata |
| 2 | No keylogger/screenshot/real capture | adapter registry and non-goals |
| 3 | Privacy before persistence | canary and raw DB/WAL tests |
| 4 | Strict Event + anchored chain | mutation/interior/tail tests |
| 5 | D1–D3 induction, D4–D5 holdout | exact partition checks |
| 6 | Only sealed sessions segment | active-boundary regression |
| 7 | Explainable context branch | tropical humidity rule |
| 8 | Governed questions and daily budget | lifecycle/bounds/idempotency tests |
| 9 | Versioned, provenance-linked memory | version/conflict/invalidation tests |
| 10 | Compilation recomputes authority | forged-state regressions |
| 11 | Minimal Skill permissions | lint and LearnPack permissions |
| 12 | Preview never executes | CLI/API/dashboard and risk contract |
| 13 | Deterministic hostile-safe LearnPack | cross-run bytes + ZIP/TOCTOU tests |
| 14 | Import inspectable but disabled | blank-profile round trip |
| 15 | Operable release artifact | CLI, loopback API/UI, CI, wheel/sdist smoke |

Success is `success_proved` only when all required reference benchmark dimensions pass. The benchmark reports a vector and no aggregate score so a privacy failure cannot be hidden by functional success.
