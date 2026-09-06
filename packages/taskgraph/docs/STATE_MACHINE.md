# State Machine

New tasks start `queued`. A dependency-ready task becomes `running` when claimed. A recoverable failure or expired lease becomes `waiting`; a later claim increments attempts. Exhaustion becomes `failed`. Dependents of `failed` or `rejected` tasks become `rejected`. Evidence-gated completion becomes `done`.

No non-`done` state is success. Replaying an identical outcome event is a no-op; conflicting reuse is rejected.

