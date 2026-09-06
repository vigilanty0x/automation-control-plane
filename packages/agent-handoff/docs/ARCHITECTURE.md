# Architecture

`models` owns bounded parsing and fail-closed invariants. `render` converts one canonical model to deterministic JSON or Markdown. `ledger` persists idempotent hash-chained events. `probes` separates process health from a functional control/counter-proof. `cli` is a thin stable boundary.

The runtime has no network or provider dependency and never executes evidence content.

