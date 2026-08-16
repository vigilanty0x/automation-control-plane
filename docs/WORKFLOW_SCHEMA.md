# Workflow schema 1.0

Every listed field is mandatory. Unknown fields are errors. The maximum serialized workflow/input JSON value is 64 KB, nesting is bounded, strings are bounded, arrays/objects are bounded, and floating-point numbers are rejected.

The machine-readable companion is [`schema/workflow-v1.0.schema.json`](../schema/workflow-v1.0.schema.json). The Python validator remains authoritative for semantic rules JSON Schema cannot fully express here, including UTF-8 byte limits, total node count, dependency existence, cycle detection, estimate totals, and retry delay ordering.

## Root object

| Field | Type | Constraint |
| --- | --- | --- |
| `schema_version` | string | Exactly `1.0`. |
| `workflow_id` | string | 1–128 characters; alphanumeric first, then alphanumeric, `.`, `_`, `:`, or `-`. |
| `version` | integer | Positive 32-bit integer. |
| `description` | string | At most 4,096 UTF-8 bytes. |
| `budget_units` | integer | `0` through `1,000,000,000`. |
| `default_deadline_seconds` | integer | `1` through one year. |
| `triggers` | array | 1–32 unique trigger objects. |
| `steps` | array | 1–256 unique step objects forming an acyclic graph. |

The sum of every step's `estimated_cost` cannot exceed `budget_units`.

## Triggers

Manual:

```json
{"type":"manual"}
```

Webhook event representation:

```json
{"type":"webhook","event":"release.requested"}
```

Fixed-interval schedule representation:

```json
{"type":"scheduled","interval_seconds":3600}
```

The control plane does not expose a write webhook or scheduler. A trusted ingress submits an exact declared trigger and supplies an idempotency key. For schedules, a useful key is a stable workflow ID plus the UTC interval bucket.

## Step object

| Field | Type | Constraint |
| --- | --- | --- |
| `id` | string | Unique workflow-local identifier. |
| `handler` | string | Name only; must also exist in the worker's trusted registry. |
| `depends_on` | string array | Unique known step IDs; no self references or cycles. |
| `input` | object | Bounded JSON copied into the immutable definition. |
| `required_capability` | string | Capability required in addition to `job:claim`. |
| `approval` | string | `none` or `required`. |
| `estimated_cost` | integer | Nonnegative budget units. |
| `timeout_seconds` | integer | `1` through 86,400. Bounds the lease request. |
| `retry` | object | Required retry policy below. |

Retry fields are `max_attempts` (1–20), `initial_delay_seconds`, integer `multiplier` (1–100), and `max_delay_seconds`. The maximum delay cannot be below the initial delay.

## Evolution policy

Schema versions are explicit because strict unknown-field rejection and silent forward compatibility are mutually exclusive. A future format will use a new `schema_version` and an explicit parser/migration path. Existing workflow versions and job-pinned definitions remain readable for the support window documented in the changelog.
