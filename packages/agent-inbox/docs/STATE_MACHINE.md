# State machine

`queued` can atomically become `running`. `running` may become `done`, `waiting`,
`rejected`, `failed`, or return to `queued` after a retryable failure. `waiting` and
non-exhausted `failed` work may be manually retried. Terminal `done` and `rejected`
states cannot be reclaimed.

Every claim increments `attempts` and issues a new opaque fencing token. Only the
current unexpired token can heartbeat or transition running work. Completion clears
all lease data and stores evidence plus its digest.

