# Agent Productivity Dashboard

## Purpose

Calculate reliability, throughput, and optional ordered trend points from bounded caller-supplied counters.

## Non-goals

The package does not observe agents, collect telemetry, compare human performance, or establish causality.

## Install

Requires Python 3.11 or newer: `python -m pip install .`

## API

`evaluate(record)` accepts `agent`, integer `completed`, `failed`, `retries`, `elapsed_ms`, and optional `trend`. Output is labeled `source: supplied-metrics` and `observed_by_tool: false`.

## CLI

Run `agent-productivity-dashboard examples/valid.json` to print a deterministic metric receipt.

## Example

The example supplies one synthetic aggregate. Trend points use strictly increasing timezone-aware `as_of` timestamps.

## Security

Booleans, negative or excessive counts, zero duration, unordered/naive times, oversized series, and non-objects fail closed.

## Limits

Counts are capped at one billion, elapsed time at one year, trend at 365 points, and aggregate input at 64 KiB.

## Tests

Run `python -m unittest discover -s tests -v` and `python scripts/check.py`.

## AI assistance

See `AI_ASSISTANCE.md`; humans should review metric definitions and their organizational use.

## License

Apache-2.0; see `LICENSE`.
