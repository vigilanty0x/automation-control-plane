# AgentOps public evidence refresh policy

The public consumer preflight is intentionally **explicit-refresh only**.

A full scan enumerates the complete public repository portfolio, downloads bounded public archives, checks source SHA drift, resolves exact code references, and may consume a meaningful GitHub API budget. Running that scan on every development commit produced installation-level API rate limiting without adding new migration evidence.

The workflow `.github/workflows/agentops-consumer-preflight.yml` therefore uses `workflow_dispatch` only and a single concurrency group with `cancel-in-progress=true`.

This has three consequences:

1. normal adapter, documentation, test, and CI commits do not burn the public enumeration quota;
2. a refresh is an explicit evidence action with an identifiable run ID and immutable head SHA;
3. an API rate-limit or transport failure cannot be mistaken for a semantic consumer-inventory regression.

The most recent **complete** evidence receipt remains authoritative for its own observation timestamp until a newer complete receipt replaces it. A failed refresh never overwrites or upgrades the previous complete evidence and never opens a gate.

A refresh should be run when one of these facts changes materially:

- the SHA-bound source inventory changes;
- the public portfolio gains/removes repositories relevant to AgentOps;
- the consumer scanner or syntax triage logic changes;
- an explicit pilot/adopter manifest becomes complete;
- the project is preparing the formal default-branch migration gate.

The formal migration evidence workflow remains separate and has stronger lineage and human-attestation requirements. Neither preflight nor a prior complete receipt authorizes aliases, consumer mutation, redirects, release publication, source retirement, or archive actions.
