# Recovery

Recovery runs explicitly and before every claim. An expired lease returns to `queued`
when `attempts < max_retries + 1`; otherwise it becomes `failed`. Lease fields are
cleared in the same transaction and a unique expiry event records the outcome.

Repeated recovery is a no-op. A reclaimed mission receives a new token, so the old
worker cannot complete or heartbeat it. SQLite provides single-host atomicity, not
distributed consensus; networked multi-host deployments need an external database.

