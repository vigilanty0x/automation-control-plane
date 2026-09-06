# Contributing

Thank you for improving Apprentice AI.

1. Open an issue for material changes and state the user problem, boundary, and non-goals.
2. Use Python 3.11+ and the standard library unless a dependency has a documented necessity and supply-chain review.
3. Keep changes scoped. Do not add real capture or execution behavior.
4. Add tests for success, abstention, malformed input, privacy, cross-profile scope, and rollback/idempotency where relevant.
5. Run `python -m unittest discover -s tests -v` and build both distributions.
6. Update public schemas and docs when contracts change.

Never place real user activity, tokens, emails, paths, screenshots, or proprietary files in tests. Use conspicuously synthetic canaries. Security reports belong in private vulnerability reporting, not a public pull request.

Commits should be reviewable and explain why the invariant holds. Pull requests must disclose AI assistance and identify which outputs were independently tested.
