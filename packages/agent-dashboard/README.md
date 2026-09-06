# Agent Dashboard

An accessible, evidence-first dashboard for agent operations. It makes running, waiting, failed, and completed work visible alongside timelines, logs, bounded retries, costs, results, provenance, and freshness.

This repository is a standalone public implementation of roadmap item **PUB-005**. It contains only generic code and synthetic fixtures.

## What ships in v0.1.0

- responsive overview, agent directory, run timeline, and evidence routes;
- a typed view contract and transport adapter;
- a bounded demo API with success, empty, degraded, timeout, and error scenarios;
- working search, status filters, timeline ranges, JSON export, evidence drawer, and state-gated retry;
- visible source provenance and data freshness;
- keyboard focus, skip navigation, controlled live announcements, reduced-motion support, and mobile navigation;
- unit, API, rendered-route, browser journey, lint, and GitHub Actions checks.

## Quick start

Requires Node.js 22.13 or newer on Linux.

```bash
npm ci
npm run dev
```

Open the local URL printed by Vite. No account, key, private repository, or external API is required.

## Demo API

```text
GET /api/snapshot?scenario=success
GET /api/snapshot?scenario=empty
GET /api/snapshot?scenario=degraded
GET /api/snapshot?scenario=timeout
GET /api/snapshot?scenario=error
```

Unknown scenarios return `400`. The browser's **State lab** calls these responses and proves every visible state. See [the view contract](docs/VIEW_CONTRACT.md) for the schema and action rules.

## Verification

```bash
npm run lint
npm test
```

`npm test` builds the deployable worker, runs adapter and API tests, and verifies every dashboard route from rendered HTML. The release journey additionally exercises real browser actions: search, status filtering, details, retry completion, state transitions, route navigation, and mobile layout.

## Project map

```text
app/
  api/snapshot/route.ts       bounded synthetic API
  components/agent-dashboard.tsx
  agents/                     agent directory route
  runs/                       timeline route
  evidence/                   acceptance-proof route
lib/
  contracts.ts                explicit data and action contract
  adapter.mjs                 transport-state normalization
  fixtures.ts                 synthetic demo dataset
tests/                        unit, API, and rendered-route checks
docs/VIEW_CONTRACT.md         states, permissions, and API boundary
```

## Safety and limitations

The included retry is a browser-only synthetic demonstration; it is not a production control-plane mutation. A production adapter must authenticate callers, authorize each action on the server, enforce idempotency and retry budgets, sanitize logs, and record immutable evidence.

Do not send secrets or production logs to this demo. See [SECURITY.md](SECURITY.md) and [AI_ASSISTANCE.md](AI_ASSISTANCE.md).

## License

Apache-2.0.
