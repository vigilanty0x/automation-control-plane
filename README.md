# Context Window Budgeter

## Purpose

Plan required and prioritized optional sections inside a bounded context window while reserving output capacity.

## Non-goals

It does not tokenize text, call a model, summarize content, or guarantee provider token accounting.

## Install

Requires Python 3.11 or newer.

```console
python -m pip install .
```

## CLI and API

Run the built-in positive and negative control:

```console
context-budget probe
```

Process JSON from a file:

```console
context-budget budget --input examples/basic.json
```

The public Python seam is `context_window_budgeter.budget`:

```python
from context_window_budgeter import budget
```

Functions return structured JSON-compatible results and reject malformed input without raising validation exceptions.

## Example

A runnable input is provided at `examples/basic.json`. CLI output is deterministic and includes either a SHA-256 evidence field or an explicit validation failure.

## Security and trust model

All section entries are validated before planning. Names are unique, integers are bounded and non-boolean, and the required flag must be a real boolean. The tool performs no network calls.

## Limitations

Windows are capped at ten million declared tokens and plans at 500 sections; counts are caller estimates.

## Tests

Run the same local gates used by CI:

```console
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
context-budget probe
context-budget budget --input examples/basic.json
```

CI tests Python 3.11 and 3.12, installs the project and rebuilt wheel, imports the installed package, and exercises both the probe and example.

## AI disclosure

AI assistance supported defensive implementation, adversarial test design, and documentation. See [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for scope and review expectations.

## License

Apache-2.0. See [LICENSE](LICENSE).

