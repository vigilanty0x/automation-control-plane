# Handoff Markdown CLI

## Purpose

Create deterministic, evidence-complete Markdown handoffs with ownership, status, risks, and next steps.

## Non-goals

It does not send messages, assign work, verify evidence, or replace an acceptance decision by the next owner.

## Install

Requires Python 3.11 or newer.

```console
python -m pip install .
```

## CLI and API

Run the built-in positive and negative control:

```console
handoff-md probe
```

Process JSON from a file:

```console
handoff-md build --input examples/basic.json
```

The public Python seam is `handoff_markdown_cli.build`:

```python
from handoff_markdown_cli import build
```

Functions return structured JSON-compatible results and reject malformed input without raising validation exceptions.

## Example

A runnable input is provided at `examples/basic.json`. CLI output is deterministic and includes either a SHA-256 evidence field or an explicit validation failure.

## Security and trust model

All interpolated fields are bounded and escaped for Markdown/HTML contexts. Evidence and next-owner fields are required and malformed structures fail closed. The tool performs no network calls.

## Limitations

Each list is capped at 100 items, output is capped at 100,000 characters, and field newlines and controls are rejected.

## Tests

Run the same local gates used by CI:

```console
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
handoff-md probe
handoff-md build --input examples/basic.json
```

CI tests Python 3.11 and 3.12, installs the project and rebuilt wheel, imports the installed package, and exercises both the probe and example.

## AI disclosure

AI assistance supported defensive implementation, adversarial test design, and documentation. See [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for scope and review expectations.

## License

Apache-2.0. See [LICENSE](LICENSE).
