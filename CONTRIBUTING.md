# Contributing

Preserve the fail-closed invariant: exceeded or unknown consumption, missing authority, invalid state, and conflicting replay must never become success.

Use synthetic fixtures only. Add contract and negative tests for state, accounting, journaling, idempotence, and concurrency-sensitive behavior. Run tests, the public-boundary check, counter-proof, and offline wheel build before submitting a change. Do not include credentials, private missions, account data, or real agent transcripts.

