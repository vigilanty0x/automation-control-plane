# HTTP API v1

The server binds only to `127.0.0.1`. `GET /health` is public and minimal. All dashboard/assets and `/api/v1/*` routes require either `Authorization: Bearer TOKEN` or the HttpOnly session cookie set by the one-use bootstrap URL. Browser cookie mutations require the exact server `Origin`.

Every `POST` requires `Content-Type: application/json`, a body of at most 1 MiB, and a unique `Idempotency-Key` (8–128 identifier characters). Identical route/body replays return the canonical stored response; reuse for a different request returns `IDEMPOTENCY_CONFLICT`. A pending outcome is never blindly re-executed. Responses include `X-Request-ID` and `Cache-Control: no-store`.

## Read routes

- `/api/v1/capabilities`, `/api/v1/profiles`
- `/api/v1/profiles/{profile}/sessions`
- `/api/v1/profiles/{profile}/sessions/{session}/verify`
- `/timeline`, `/episodes[/id]`, `/routines[/id]`, `/questions`
- `/memories[/id]`, `/skills`, `/imports[/id]`, `/audit`

Timeline accepts bounded `limit`, `offset`, and optional `session_id` query fields.

## Mutation routes

- `POST /api/v1/demo` — full automatic reference proof.
- `POST /api/v1/demo/observe` — stop at the human question gate.
- `POST /api/v1/profiles` with `{"name":"…"}`.
- `POST .../episodes/build`, `.../routines/discover`.
- `POST .../routines/{id}/questions`, `.../compile`.
- `POST .../questions/{id}/answer|dismiss|snooze|resume|expire`.
- `POST .../skills/{skill}/{version}/preview`.
- `POST .../bench/run`, `.../purge`.

No API route executes a skill, reads an arbitrary import path, exports to an arbitrary path, or enables a quarantined pack.

Errors use `{"error":{"code":"…","message":"…"}}`. Validation is 400, authentication/policy 403, missing resources 404, integrity/idempotency conflict 409, oversized bodies 413, timeouts 408, and internal failures 500.
