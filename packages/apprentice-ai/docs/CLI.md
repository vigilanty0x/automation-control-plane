# CLI reference

Global options are `--data-dir PATH` and `--json` (JSON is always emitted). Success is exit 0; validation/not-found/usage is 3, policy stop is 4, integrity is 5, unexpected internal failure is 70, and interruption is 130.

## Commands

| Command | Purpose |
|---|---|
| `version`, `capabilities` | Version and honest feature matrix |
| `init --name NAME`, `profiles list` | Local profile lifecycle |
| `demo [--output PACK]` | Full proven D1–D5 reference pipeline |
| `ingest PROFILE FILE [metadata]` | Strict JSONL adapter |
| `timeline list/verify` | Read minimized events and verify a session chain |
| `episodes build/list/show` | Segment only sealed sessions |
| `routines discover/list/show` | Derive and inspect candidates |
| `questions generate/list/answer/dismiss/snooze/resume/expire` | Govern ambiguity and human decisions |
| `memory list/explain/invalidate-evidence` | Inspect provenance and stale dependents |
| `skill compile/list/preview` | Compile reviewed evidence and preview without execution |
| `pack export/validate/inspect/import/list/show` | Deterministic packaging and quarantine |
| `privacy audit/scan/purge-profile` | Inspect filtering and delete a profile |
| `bench run PROFILE` | Vector benchmark without an aggregate score |
| `serve [--port PORT] [--token TOKEN]` | Authenticated loopback dashboard/API |

Run `apprentice COMMAND --help` for positional and option details. IDs are opaque and must be copied exactly from prior JSON output. Purge requires `--confirm PROFILE`.

`skill preview --inputs FILE` reads a strict JSON object, privacy-sanitizes it, and returns planned steps. It does not open the referenced dataset or execute a step.
