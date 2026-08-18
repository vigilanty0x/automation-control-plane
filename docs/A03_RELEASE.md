# Model Router A03 release contract

This document adds release-quality gates without changing the product identity or inventing a new package version.

## States

- `PREPARED`: the exact source SHA passes the existing test suite and produces verified installable artifacts, checksums and SBOM.
- `ATTESTED`: the canonical wheel has GitHub/Sigstore SLSA provenance that passes strict verification.
- `TAGGED`: an explicit tag exists after review.
- `RELEASED`: the expected immutable assets were published under that tag.
- `VERIFIED`: a separate read-back verifies tag target, hashes, provenance, installability and smoke behavior.
- `BLOCKED`: one or more required gates are missing or red.
- `ROLLED_BACK`: the A03 merge is reverted or consumers are restored to the last independently verified release while failed evidence is retained.

`release-policy.a03.v1.json` keeps `publish_enabled=false`; A03 CI cannot establish `TAGGED`, `RELEASED`, or `VERIFIED`.

## Pre-publication evidence

The exact candidate must pass Ubuntu, Windows and macOS on CPython 3.11–3.13, the repository's existing unit/contract tests, wheel plus sdist build, clean installed-artifact smoke, SHA-256 verification, CycloneDX 1.6 SBOM generation, a deliberate tampering counter-proof, and strict GitHub/Sigstore provenance verification.

## Rollback

No A03 release is authorized without rollback. Because this hardening does not force a package-version change, the rollback is the exact inverse of the A03 merge (Git revert) or restoration of the last independently verified published artifact. Evidence from the failed candidate must be preserved for audit rather than deleted to obtain a green state.

## Publication and archive gates

Publication is a separate reviewed operation that must bind one exact source SHA and add a read-only post-publication verifier. Portfolio consolidation is not archive authorization; archival still requires consumer inventory, compatibility/redirect evidence, rollback proof, and explicit human approval.
