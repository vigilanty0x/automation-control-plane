# Evidence and metrics

Each operation result contains its operation ID, decision, action, reason, reservation snapshot, and SHA-256 evidence identity. Reservations expose reserved, consumed, and released state.

Journal event IDs hash operation ID and the full result. The journal rejects truncated JSON, modified payloads, duplicate event identities, and conflicting reuse.

Metrics preserve cumulative consumption, rejection count, intervention count, and mission counts by state. A blocked unknown measurement increments interventions and remains visible as failed state.

