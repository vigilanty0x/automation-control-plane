# Contract 1.0

Each handoff identifies a mission, monotonic sequence, current state, sender, receiver, accountable owner, bounded relative path scope, capabilities, permissions, numeric limits, acceptance criteria, evidence, and open items.

States are `queued`, `running`, `waiting`, `failed`, `rejected`, and `done`. The schema intentionally preserves unfinished and rejected work. A consumer must not interpret `waiting` as success.

Evidence requires kind, URI, summary, and SHA-256. The tool validates shape and identity; a separate reviewer must verify that the referenced bytes actually exist and support the claim.

