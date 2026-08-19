# Apprentice AI A03 release contract

A03 hardens release evidence without changing Apprentice AI's `0.1.0` product identity or its preview-only safety boundary.

## Safety invariant

This work does **not** add execution of learned actions. Apprentice AI remains local-first and preview-only: learned routines can be inspected, compiled, previewed, exported, and imported into quarantine, but A03 does not authorize browser automation, shell execution, cloud model calls, telemetry, or other external effects.

## Build backend

The package requires `setuptools>=83.0.0,<84`, avoiding the older unsupported/vulnerable pin. A03 CI narrows the controlled release environment further to exact `setuptools==83.0.0`, `pip==25.2`, and `wheel==0.45.1` so candidate evidence is reproducible without changing the package's supported build range.

## States

- `PREPARED`: the exact source SHA passes the existing security/behavior suite and produces verified installable wheel + sdist, SHA-256 checksums, and CycloneDX SBOM.
- `ATTESTED`: the canonical wheel has GitHub/Sigstore SLSA provenance that passes strict verification.
- `TAGGED`: an explicit reviewed tag exists.
- `RELEASED`: expected immutable assets were published under that tag.
- `VERIFIED`: a separate read-back verifies tag target, hashes, provenance, installability, and smoke behavior.
- `BLOCKED`: one or more required gates are missing or red.
- `ROLLED_BACK`: the A03 merge is reverted or consumers are restored to the last independently verified `0.1.0` artifact while failed evidence is preserved.

`release-policy.a03.v1.json` keeps `publish_enabled=false`; A03 CI cannot establish `TAGGED`, `RELEASED`, or `VERIFIED`.

## Pre-publication evidence

The exact candidate must pass Ubuntu, Windows, and macOS on CPython 3.11–3.13, the repository's complete existing test suite, real installed-wheel CLI smoke, source-distribution installation, deterministic LearnPack demo generation, SHA-256 verification, CycloneDX 1.6 SBOM generation, a deliberate tampering counter-proof, and strict GitHub/Sigstore provenance verification.

## Rollback

Because A03 does not force a package-version change or persistent-state migration, rollback is the inverse of the A03 merge or restoration of the last independently verified `0.1.0` artifact/source. Failed candidate evidence must be retained for audit rather than deleted to obtain a green status.

## Publication and archive gates

Publication is a separate reviewed operation that must bind one exact source SHA and add a read-only post-publication verifier. No repository archive, deletion, tag, GitHub Release, or package publication is authorized by this A03 change.
