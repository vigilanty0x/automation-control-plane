# Apprentice AI

Apprentice AI is a local-first, preview-only reference implementation of a digital apprentice: it turns explicitly permitted synthetic or JSONL activity into inspectable episodes, explainable routine candidates, governed questions, versioned memory assertions, Skill IR, and deterministic LearnPacks.

Release `0.1.0` deliberately does **not** execute learned actions. It has no keylogger, screenshot capture, browser automation, shell execution, cloud model, telemetry, or runtime dependency. The only bundled learning proof is the synthetic D1–D5 laboratory-export scenario.

## What is proved

- A closed Event contract is filtered before persistence.
- SQLite events form an anchored SHA-256 hash chain; active and sealed tail truncation is detected.
- D1–D3 are induction demonstrations and D4–D5 are disjoint holdouts.
- The deterministic baseline discovers the `climate == tropical` humidity-correction branch.
- A human answer is linked atomically to the question, routine, and versioned procedural memory.
- Compilation recomputes profile-scoped provenance from the store and supports one reviewed reference template.
- Preview returns a plan with `execution_allowed: false`, network denied, and no external effects.
- LearnPack export is deterministic across independent D1–D5 runs; import is inspectable but remains `disabled_untrusted`.

These claims are limited to the bundled synthetic scenario. Unknown goals abstain, unsupported routines fail with `UNSUPPORTED_ROUTINE_TEMPLATE`, and no general desktop learning claim is made.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install .
apprentice --data-dir .demo demo --output reference.learnpack
apprentice --data-dir .demo capabilities
apprentice --data-dir .demo serve --port 8765
```

`serve` prints a one-use loopback bootstrap URL. The HTTP server binds only to `127.0.0.1`; its dashboard guides a manual question → answer → compile → preview loop. Every mutating API request requires an `Idempotency-Key`.

For a manual CLI flow:

```bash
apprentice --data-dir .local init --name "My local profile"
apprentice --data-dir .local ingest PROFILE events.jsonl --goal fixture_goal --effect fixture_effect
apprentice --data-dir .local timeline list PROFILE
apprentice --data-dir .local episodes build PROFILE
```

The JSONL adapter accepts one strict Event object per line (64 MiB file, 1 MiB line, 10,000 event limits), replaces the source with its registered adapter identifier, runs the privacy guard, and seals the session. It never copies the input file.

## Architecture

```text
permitted fixture/JSONL
        │
        ▼
 closed Event contract ──► privacy guard ──► SQLite hash-chain
                                                   │
                          sealed sessions only ◄───┘
                                  │
                     episodes → routine + holdout
                                  │
                         governed human question
                                  │
                     versioned memory + provenance
                                  │
                      Skill IR → preview (no execute)
                                  │
                 deterministic LearnPack → quarantine import
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).

## CLI and API

The CLI always emits JSON and stable application error codes. Run `apprentice --help`, or see [docs/CLI.md](docs/CLI.md).

The dependency-free HTTP API exposes authenticated loopback resources for profiles, sessions, timeline, episodes, routines, questions, memories, skills, imports, audit, benchmarks, and non-executing previews. See [docs/API.md](docs/API.md).

## Verification

```bash
python -m unittest discover -s tests -v
python -m build
```

The suite covers privacy canaries, raw SQLite/WAL scans, chain tampering and truncation, cross-profile access, hostile archives, TOCTOU snapshots, forged holdouts, stale evidence, idempotency, CSRF/Host/body handling, CLI subprocesses, the dashboard API, and cross-run archive reproducibility.

## Project status

Alpha reference release. The roadmap prioritizes adapters that preserve explicit consent, generalized routine templates with honest abstention, migration tooling, signatures, and reproducible third-party conformance suites. Real action execution is outside `0.1.x`.

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
