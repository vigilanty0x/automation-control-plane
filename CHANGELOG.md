# Changelog

## 0.1.0 - 2026-08-15

- Add deterministic per-mission branches and linked worktrees.
- Add prefix-aware path ownership with visible conflict rejection.
- Add durable explicit states, events, idempotency, bounded retries, and interventions.
- Reuse existing mission Git resources during retries and recovery.
- Require clean Git state, exact HEAD, owned diffs, artifacts, tests, producer, and criteria before `done`.
- Refuse cleanup until Git proves integration; remove without force and delete only merged branches.
- Add managed/unmanaged audit, operating metrics, bounded JSON CLI, synthetic demo, tests, and CI.
