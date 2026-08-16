# Architecture

## Boundary-first design

Apprentice AI 0.1.0 is a modular monolith with a deliberately narrow trust boundary. `PrivacyGuard` and the closed Event contract run before `EventStore.append_event`; durable state is never the first place untrusted activity is interpreted.

The package has seven layers:

1. `strictjson.py` — bounded UTF-8 JSON, duplicate-key and non-finite-number rejection.
2. `privacy.py` — closed Event shape, denied application/domain policy, semantic secret-key and pattern redaction.
3. `store.py` — profile-scoped SQLite, WAL, append-only events, incremental/final hash anchors, governed state transitions.
4. `ingest.py` and `synthetic.py` — registered adapters. JSONL uses a no-follow descriptor and actual streaming bounds; D1–D5 is deterministic logical reference data.
5. `learning.py` — explicit-boundary segmentation, LCS routine baseline, context branch discovery, holdout evaluation, question and memory lifecycle.
6. `skills.py` and `learnpack.py` — store-revalidated compilation, non-executing preview, deterministic package and quarantine import.
7. `cli.py`, `api.py`, and `web/` — JSON CLI and authenticated loopback UI/API.

## State model

Sessions start `active` and terminate as `completed`, `incomplete`, or `stopped`. Only terminal sessions with a valid anchored chain are segmented. Routines progress `observed → explained → confirmed → compilable`; evidence invalidation moves dependent routines and skills to `stale`. Question transitions are explicit, and terminal answers are idempotent.

Memory is append-only and versioned. The reference compiler requires a confirmed procedural memory linked to the affirmative answer and the exact D1–D5 evidence partition. Raw public `put_*` persistence methods are not authority: compilation recomputes canonical evidence from related tables.

## Integrity model

Each event digest is `SHA256(previous_hash || canonical_event_without_integrity)`. The session row stores `event_count` and `head_hash` atomically on every append and again on close. Verification checks sequence continuity, links, envelope digests, and the anchor, detecting mutation, interior deletion, and tail truncation.

This is tamper-evidence inside the local database, not a remote timestamp, hardware root of trust, or defense against an administrator rewriting every row and anchor.

## Concurrency and idempotency

SQLite writes use `BEGIN IMMEDIATE` under a process lock. The API reserves an idempotency key durably before mutations. Completed 2xx/4xx responses are replayed byte-equivalently after privacy canonicalization; key reuse with a different route/body conflicts. A durable `pending` state prevents blind replay after an unknown crash outcome.

## Extension rules

New adapters must use a registered source identifier, define minimization, pass the same privacy boundary, seal sessions on all outcomes, and ship synthetic adversarial tests. New compilation templates must define typed inputs, pre/postconditions, explicit permissions, induction/holdout provenance, and honest abstention outside their scope.
