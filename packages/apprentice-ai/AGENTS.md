# Agent guidance

Scope all changes to this repository. Preserve the local-first, preview-only contract.

Before editing, read `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, and relevant tests. Use small patches and fresh verification. Do not weaken bounds, profile scoping, privacy-before-persistence, chain anchors, store evidence recomputation, quarantined imports, loopback authentication, or `execution_supported: false`.

Never add real credentials or observations. Use synthetic fixtures. Do not commit generated databases, packs, build artifacts, or environment directories. Changes to a public contract require schema, docs, adversarial tests, and changelog updates.
