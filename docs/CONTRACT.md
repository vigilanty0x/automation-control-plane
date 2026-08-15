# Contract

Mission payloads are canonical JSON objects up to 100 KB. Priorities are integers
0–100, with larger values first. Capability, permission, and ownership lists are
unique and bounded to 100 entries. Retries are 0–20. Agent limits bound simultaneous
running missions and leases to 1–86400 seconds.

An exact idempotency replay returns the original mission. Reusing the key with any
different logical field raises a conflict. Event IDs provide the same protection for
disagreements and escalations.

Completion requires one or more passed tests and at least one commit or artefact. All
test observations must be `passed`; a failed or skipped observation keeps the mission
non-done. Evidence is canonicalized and addressed by SHA-256.

