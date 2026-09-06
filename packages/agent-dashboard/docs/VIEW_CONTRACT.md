# View contract

Agent Dashboard consumes a deliberately small, public contract. The default adapter is the synthetic route at `GET /api/snapshot`.

## States

| State | HTTP evidence | Data shown | Retry |
| --- | --- | --- | --- |
| `ready` | `200` with a valid snapshot | Current agents and events | No |
| `empty` | `200` with zero agents | Explicit empty state | Load live snapshot |
| `degraded` | `206` with warnings | Operational data plus warning | Yes |
| `timeout` | `504` | No cached success is implied | Yes |
| `error` | `5xx` or invalid payload | No cached success is implied | When safe |
| `loading` | Client request in flight | Skeleton only | No concurrent request |

## Required snapshot fields

- `provenance`: source, generation identity, schema version, synthetic flag, and fetch timestamp.
- `agents`: status, task, run identifier, cost, tokens, progress, retry counts, logs, result, and allowed actions.
- `events`: chronological run evidence linked to an agent and run.
- `budgetUsd`: bounded daily demo budget.

The browser exposes retry only when `allowedActions` contains `retry`, the run is failed, and `retries < maxRetries`. All other visible controls either perform their named action or are disabled with an explanation.

## Bounded demo API

`GET /api/snapshot?scenario=<value>` accepts only `success`, `empty`, `degraded`, `timeout`, or `error`. Any other value returns `400`. The endpoint sets `Cache-Control: no-store` and `X-Data-Provenance: synthetic-demo-api`.
