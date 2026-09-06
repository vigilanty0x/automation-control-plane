# Native execution quotas

An optional `budget.execution_quota` adds durable admission and consumption
accounting to the existing Factory Store. It is a quota for native Executor
invocations within **one run**, not an account-wide provider quota. No additional
queue, model selector, identity authority or remote service is introduced.

Omitting the option preserves canonical spec JSON/digests, receipts, journal
events and exported JSON. A full pre-change export is a byte comparison fixture.
The additive database schema is version 3; migration from versions 1 and 2 keeps
old records. Older binaries cannot open version 3: retain a verified backup for
a binary downgrade. There is no automatic destructive downgrade or purge.

## Input

Add this object inside an otherwise valid Factory budget:

```json
{
  "execution_quota": {
    "limits": {
      "executor_calls": 20,
      "retained_output_bytes": 10485760,
      "execution_ms": 600000
    },
    "owners": {
      "writer": {
        "executor_calls": 10,
        "retained_output_bytes": 5242880,
        "execution_ms": 300000
      }
    }
  }
}
```

`limits` is required. `owners` is optional; its keys must identify existing task
owners. Owner means a declared quota group, not an authenticated actor. At most
1,000 groups are accepted. Each limit vector contains exactly the three shown
resources, using non-boolean integers. Calls are in 0..10,000; other quantities
are in 0..1,000,000,000,000. Unknown units, booleans, negatives, floats, overflow,
null, missing resources and unknown fields are rejected. A zero limit is valid
and refuses admission. Unknown owner names are rejected, not silently ignored.

## Measurements and limits

| Resource | Reservation before claim | Observed consumption |
| --- | --- | --- |
| `executor_calls` | One command plus all declared tests | One per actual invocation of `Executor.execute` |
| `retained_output_bytes` | Sum of the existing per-command output caps | `len(result.stdout) + len(result.stderr)` on raw bytes |
| `execution_ms` | Sum of command/test timeouts, rounded upward to milliseconds | Monotonic elapsed time around each invocation, rounded upward |

The streams are raw captured prefixes. They are not decoded or re-encoded for
accounting; a cap may end in the middle of a multibyte character. Truncation may
retain only a prefix while more bytes were emitted. `bytes_seen` remains separate
receipt information and is not the retained-byte resource. Artifact files,
workspace copies and database storage are not counted by this quota.

Elapsed measurement excludes reservation writes, workspace/evidence work and
publication; the existing active-wall budget covers those paths. A timeout is
an admission bound, not an OS guarantee of exact termination time. Cleanup or
an uncooperative executor can exceed it. Such an overrun is recorded without
clamping, retains capacity, vetoes publication and blocks subsequent dispatches.
The implementation does not claim that physical resource use can never exceed
a declared limit. It guarantees atomic admission against held/settled amounts.

The native subprocess and deterministic mock executors explicitly expose the
`factory-executor-output-v1` output contract. The Engine measures calls, returned
bytes and monotonic time itself, rather than trusting `duration_seconds` from a
result. Origin is recorded as `engine_monotonic_output`. Direct Store clients
default to `caller_declared` origin. Neither origin authenticates a producer;
the trusted local caller can invoke APIs or modify its own database. A mock is
not evidence that a provider was invoked. Missing output capability is rejected
before dispatch, once, without retrying the unsupported operation.

Tokens, input/output token totals, currency, price, GPU time and provider call
counts are **not measured** by this feature. They remain null in usage reports.
No tokenization is inferred from characters or output bytes. LocalAI's observed
output-token count does not establish input-token usage or billing; PromptOps
recorded replays do not establish a fresh model call. A requested token/cost
quota is explicitly unsupported. Model routing and receipt-based provider
reservations remain separate, unfinished integration work.

## Durable execution and recovery

The claim transaction reserves the whole static attempt under `BEGIN IMMEDIATE`.
Run/task/attempt/spec, declared group, exact command/environment digests, test
order and per-step caps are bound to the reservation. Global run and group
limits include settled consumption plus held capacity. Two processes cannot
claim capacity that exceeds these limits. Different runs have separate budgets.

`begin_dispatch` validates the live worker lease and records STARTED before the
call. The same dispatch ordinal cannot be issued again. `settle_dispatch`
records a complete measurement; an identical settlement is idempotent and a
changed one is rejected. Failed measured calls remain consumed. Undispatched
tests release their unused capacity only at verified completion.

An exception or lost result becomes UNKNOWN. `consumption` is then null;
`known_consumption` is only the known subtotal. The full affected reservation is
retained. Expired or cancelled attempts with any dispatched work are not
automatically reissued. A reservation that provably never dispatched can be
released and a later attempt claimed. Stale owners cannot settle or publish.
This is conservative recovery, not an exactly-once guarantee for arbitrary
external effects. There is no force-release/force-zero operator bypass here.

Exhausted or uncertain quota produces explicit `waiting_quota` when pending work
remains. `run` returns promptly (CLI exit 3), without a retry/sleep loop. Quota
and approval waits share one active-wall clock; their overlap is not subtracted
twice. Unknown/overrun tasks are terminal failures or cancellations. At most
10,000 reservation records per run are admitted; reaching retention capacity
refuses further work and preserves all records. No automatic purge occurs.

## Evidence and compatibility

Reservations, dispatch states and measurements use the native hash-chained
journal and the same SQLite database. Runtime reads compare table rows to a
causal journal replay. Exports additionally compare receipts and status totals
to the replay, including exact number types, static commands and output counts.
A hash alone is not accepted as a measurement. Missing, malformed, reordered,
changed or unlinked rows are refused; a regressed or non-finite clock is refused.

Protected tasks continue to require the CONS-02 attempt approval. Dynamic
Provider requests are refused for quota-bound tasks because their effective
command/test demand would differ from the reserved specification. Unprotected
tasks without an execution quota retain their existing Provider behavior.

This feature does not sandbox arbitrary task commands. Existing ownership,
leases, kill switch, tests, artifact verification and publication fences remain
in force. The default CLI is a trusted local execution interface.
