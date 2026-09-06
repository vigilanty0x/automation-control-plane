# Mission state machine

- `queued` → `running` or `rejected`
- `running` → `waiting`, `failed`, or `done`
- `waiting` → `running`, `failed`, or `rejected`
- `failed`, `rejected`, and `done` are terminal

An accepted first reservation moves a queued mission to running. Missing capability/permission or budget rejection moves it to rejected. Unknown or excessive consumption moves it to failed. Retry is permitted only from waiting and only below the registered retry ceiling.

