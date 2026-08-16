# Security policy

## Supported versions

Security fixes are provided for the latest `1.x` release. The original `0.1.x` stateless API remains compatibility-tested but does not provide persistence or execution governance.

## Reporting a vulnerability

Use GitHub private vulnerability reporting. Do not open a public issue containing exploit details, secrets, private data, or production records. Include the affected version, prerequisite trust level, minimal synthetic reproduction, impact, and any proposed mitigation. Maintainers should acknowledge a complete report within seven days and coordinate disclosure after a fix is available.

## Deployment trust

This package authenticates no humans or services. The surrounding application must map an authenticated actor to the `principal` argument. The CLI is a local administration boundary; anyone who can invoke it with database write permission or directly modify SQLite should be treated as a control-plane administrator.

The optional HTTP server is loopback-only and read-only. It must not be reverse-proxied or exposed as a remote management interface without an independently reviewed authentication, authorization, transport, and data-redaction layer.

## Handler safety

Only reviewed callables registered by trusted startup code may execute. Registration binds each handler to an authoritative capability; workflow JSON may require more authority but cannot weaken that binding. Never add a generic command, interpreter, dynamic import, URL, arbitrary SQL, or arbitrary filesystem handler. A handler that crosses a real side-effect boundary must validate authorization there, use an external idempotency key derived from job/step identity, honor the deadline, and avoid embedding secrets in results/errors.

In-process Python cannot be forcefully terminated safely. Long-running or untrusted work belongs in a separately isolated system with its own bounded protocol; the control plane can govern the request but must not execute the untrusted payload itself.

## Data handling

Do not store credentials, tokens, private client data, production topology, or sensitive prompts in workflows, payloads, approval reasons, results, audit events, examples, or issues. SQLite and backups are plaintext and must be protected with operating-system permissions and deployment-appropriate encryption at rest.

## Audit guarantee

The SHA-256 event chain plus database-held event-count/head anchors detects accidental or partial event mutation and tail deletion. Successful results are checked against a canonical result digest and their anchored success event; outbox topics and envelopes are re-derived from the same event. These are not signatures: an attacker with full database write access can recompute all rows and anchors. Periodically checkpoint the verified `(events, head_hash)` pair in independent append-only storage if non-repudiation or privileged-tamper detection is required.

Backup and restore paths are local-administrator boundaries. Exact symlink sources/destinations are refused, backup publication never overwrites an existing name, and restore validates the complete schema, SQLite/foreign-key integrity, audit anchors, and result receipts before atomic replacement. Protect every parent directory against untrusted rename or symlink races.

## Security checks

Before release, run:

```bash
python -m unittest discover -s tests -v
python scripts/check.py
python -m pip install "build==1.2.2.post1" "setuptools==80.9.0" "wheel==0.45.1"
python -m build --no-isolation
```

Review [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for security properties, abuse cases, residual risks, and the review checklist.
