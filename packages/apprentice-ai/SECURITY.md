# Security policy

## Supported versions

Only the latest `0.1.x` release receives security fixes during the alpha phase.

## Reporting

Use GitHub private vulnerability reporting for the repository. Do not open a public issue containing an exploit, secret, personal data, or hostile LearnPack. Include the affected version, minimal reproduction, impact, and whether the report uses only synthetic data.

Maintainers should acknowledge a complete report within seven days. No bounty or response-time guarantee is promised.

## Security invariants

- No real keylogger, screenshot recorder, credential capture, shell executor, or learned-action executor exists.
- The HTTP server binds to `127.0.0.1`, validates `Host`, uses a one-use bootstrap ticket, a distinct HttpOnly session secret, strict same-origin checks for cookie mutations, no CORS, and a restrictive CSP.
- Every API mutation requires a bounded `Idempotency-Key`; authentication material is forbidden as a key.
- Untrusted JSON, JSONL, archive paths, and ZIP members are bounded and reject symlinks or traversal.
- LearnPack validation operates on one no-follow snapshot. Import never extracts or executes the archive.
- Profile-scoped reads and writes prevent cross-profile object access.
- Stale evidence is excluded from active listings and blocks preview/export; explicit
  `include-stale` inspection remains available for diagnosis.
- On POSIX, the state directory is enforced as `0700` and SQLite, WAL, and shared-memory files as `0600`. Existing paths are type- and owner-checked before any mode change; unsafe links or ownership fail closed.

Windows does not expose equivalent POSIX mode bits. Use an account-private NTFS
directory with per-user ACLs; Apprentice still rejects a final-path symlink and
non-regular state paths there.

## Out of scope claims

The local hash chain is not protection against a fully privileged attacker rewriting the database and anchors. The project is not a malware sandbox, endpoint security product, password manager, or compliance certification. Imported LearnPacks remain untrusted even after structural validation.

See [THREAT_MODEL.md](THREAT_MODEL.md) for assets, actors, and mitigations.
