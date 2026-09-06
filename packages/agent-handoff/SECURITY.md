# Security Policy

Report vulnerabilities through GitHub private security reporting.

The parser rejects unknown fields, oversized input, unsafe relative scopes, invalid IDs, malformed hashes, naive timestamps, and invalid state transitions. Ledger hashing detects accidental or unauthorized record mutation but does not provide authenticity against a privileged attacker; sign or store ledgers in a trusted system when authenticity is required.

Never place secrets or private project context in public handoff files.

