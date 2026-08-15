# Agent Session Recorder

## Purpose

Build and verify bounded SHA-256 hash chains for synthetic agent-session events.

## Non-goals

It does not capture live sessions, persist records, encrypt content, sign events, or establish authorship by itself.

## Install

Requires Python 3.11 or newer.

```console
python -m pip install .
```

## CLI and API

Run the built-in positive and negative control:

```console
agent-session-recorder probe
```

Process JSON from a file:

```console
agent-session-recorder record --input examples/basic.json
```

The public Python seam is `agent_session_recorder.record`:

```python
from agent_session_recorder import record
```

Functions return structured JSON-compatible results and reject malformed input without raising validation exceptions.

## Example

A runnable input is provided at `examples/basic.json`. CLI output is deterministic and includes either a SHA-256 evidence field or an explicit validation failure.

## Security and trust model

Verification checks every stored previous hash, event hash, count, and head. Internal consistency proves integrity only; authenticity requires a separately trusted expected head supplied to verify. The tool performs no network calls.

## Limitations

At most 10,000 events, 65,536 serialized bytes per event, and 10 MB total event content are accepted.

## Tests

Run the same local gates used by CI:

```console
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
agent-session-recorder probe
agent-session-recorder record --input examples/basic.json
```

CI tests Python 3.11 and 3.12, installs the project and rebuilt wheel, imports the installed package, and exercises both the probe and example.

## AI disclosure

AI assistance supported defensive implementation, adversarial test design, and documentation. See [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for scope and review expectations.

## License

Apache-2.0. See [LICENSE](LICENSE).

