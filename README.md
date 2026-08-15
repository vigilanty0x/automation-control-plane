# Worktree Conflict Visualizer

## Purpose

Find overlapping declared paths across bounded worktree inventories and emit deterministic Mermaid evidence.

## Non-goals

It does not inspect Git worktrees, merge branches, predict semantic conflicts, or modify files.

## Install

Requires Python 3.11 or newer.

```console
python -m pip install .
```

## CLI and API

Run the built-in positive and negative control:

```console
worktree-conflicts probe
```

Process JSON from a file:

```console
worktree-conflicts analyze --input examples/basic.json
```

The public Python seam is `worktree_conflict_visualizer.analyze`:

```python
from worktree_conflict_visualizer import analyze
```

Functions return structured JSON-compatible results and reject malformed input without raising validation exceptions.

## Example

A runnable input is provided at `examples/basic.json`. CLI output is deterministic and includes either a SHA-256 evidence field or an explicit validation failure.

## Security and trust model

Worktree names and paths are untrusted. Names are unique and bounded, paths are normalized safe relatives, repeated paths within one worktree fail closed, and Mermaid uses opaque IDs. The tool performs no network calls.

## Limitations

At most 100 worktrees, 5,000 paths per worktree, and 10,000 path entries globally are accepted.

## Tests

Run the same local gates used by CI:

```console
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
worktree-conflicts probe
worktree-conflicts analyze --input examples/basic.json
```

CI tests Python 3.11 and 3.12, installs the project and rebuilt wheel, imports the installed package, and exercises both the probe and example.

## AI disclosure

AI assistance supported defensive implementation, adversarial test design, and documentation. See [AI_ASSISTANCE.md](AI_ASSISTANCE.md) for scope and review expectations.

## License

Apache-2.0. See [LICENSE](LICENSE).

