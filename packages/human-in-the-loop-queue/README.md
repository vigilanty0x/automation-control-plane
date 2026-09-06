# Human in the Loop Queue

## Purpose

Evaluate a review request against a host-controlled UTC clock, including expiry, terminal decisions, and audited renewal.

## Non-goals

This package is not a durable queue, identity provider, approval UI, authorization service, or audit-log store.

## Install

Requires Python 3.11 or newer: `python -m pip install .`

## API

`evaluate(record)` requires `request_id`, timezone-aware `expires_at`, `decision`, and ordered audit entries with timezone-aware `at` timestamps. The untrusted record cannot provide `as_of`.

## CLI

Run `human-in-the-loop-queue examples/valid.json`. The production CLI uses the real current UTC time and has no clock-override option.

## Example

The synthetic example expires in 2099 and is pending at current production time. A `renewed` audit item must occur no later than the active expiry and supply a later `new_expires_at`.

## Security

A request is expired when effective expiry is less than or equal to the host clock. Approved/rejected decisions require matching pre-expiry audit evidence; late renewal is rejected. Supplying `as_of` in the record fails closed.

## Limits

At most 500 ordered audit records and 64 KiB aggregate input. Passing a clock callable to the Python API is test-only simulation: its receipt has `status: simulated` and `authorization_evidence: false`. Caller-supplied actor names and audit data are not authenticated.

## Tests

Run `python -m unittest discover -s tests -v` and `python scripts/check.py`.

## AI assistance

See `AI_ASSISTANCE.md`; humans remain responsible for authorization policy.

## License

Apache-2.0; see `LICENSE`.
