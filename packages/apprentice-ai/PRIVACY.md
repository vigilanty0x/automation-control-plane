# Privacy model

Apprentice AI follows minimization before persistence. The 0.1.0 release stores semantic Event references, not raw keystrokes, screenshots, clipboard contents, or files.

## Collection

Available adapters are:

- `synthetic-office/0.1.0`: generated D1–D5 fixture only.
- `jsonl-import/0.1.0`: explicit local file import with strict limits and a closed Event shape.

No background capture starts automatically. The JSONL source file is not copied. Unknown context/action/application fields are rejected instead of becoming free-text storage.

## Filtering

The privacy guard denies configured applications/domains, normalizes trailing-dot domains, redacts sensitive semantic keys at any depth, redacts known token/personal-data patterns, and rejects unbounded or non-JSON structures. Session metadata, audit details, idempotent responses, Skill previews, and pack contents cross persistence scanning too.

Redaction is defense in depth, not a promise to detect every possible secret. Keep real credentials and personal information out of fixtures and imports.

## Retention and deletion

Data remains in the selected local directory until deleted. `privacy purge-profile PROFILE --confirm PROFILE` removes all profile content, linked answers/conflicts, profile-scoped idempotency caches, checkpoints/truncates WAL, vacuums SQLite, and retains only a non-sensitive `[deleted]` tombstone for referential clarity. Deleted profiles reject new writes.

On POSIX, that directory is owner-only (`0700`) and the SQLite database family is
owner read/write only (`0600`). Windows deployments should use per-user NTFS ACLs.
These controls complement, rather than replace, encrypted storage.

LearnPack exports include abstract synthetic evidence only. Imports persist a bounded inspectable bundle under `disabled_untrusted`; they never gain execution authority.

## Network

The runtime has no telemetry or cloud model integration. Skill permissions require network `deny`. The dashboard communicates only with its loopback server.
