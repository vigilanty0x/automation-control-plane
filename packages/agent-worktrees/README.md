# Agent Worktrees

Agent Worktrees is a safety-first CLI for giving each automated agent an isolated Git branch, linked worktree, explicit file ownership, durable mission state, and machine-readable completion proof.

This standalone public project implements roadmap item **PUB-007** with only generic source code and synthetic fixtures. It has no runtime dependency beyond Python 3.11+ and Git.

## What it protects

Parallel coding becomes expensive when two workers edit the same subsystem, a retry creates a second branch, or cleanup deletes unmerged work. Agent Worktrees makes those failures explicit:

- one deterministic branch and worktree per idempotent mission;
- prefix-aware ownership locks for repository-relative paths;
- persisted `queued`, `running`, `waiting`, `failed`, `rejected`, and `done` states;
- retries that reuse the original mission, branch, and worktree;
- committed-diff checks that reject changes outside declared ownership;
- completion gates for clean status, exact HEAD, producer, tests, artifacts, and criteria;
- cleanup only after the mission branch is integrated or its owned tree matches the base after a squash;
- recovery for missing or mismatched worktrees;
- audit output separating managed, missing, mismatched, and unmanaged worktrees;
- durable events and metrics for pass@1, retries, rejection, wall time, cleanup, and human intervention.

The CLI never calls a remote, force-removes a worktree, force-deletes a branch, or deletes an arbitrary directory.

## Quick start

```bash
python -m pip install -e .
agent-worktrees demo --workspace /tmp/agent-worktrees-demo
```

The demo creates a disposable repository, provisions an isolated mission, commits a synthetic change, verifies evidence, fast-forwards `main`, removes the linked worktree with Git, deletes the merged branch, and prints the complete event trail.

## Real repository flow

Register a mission. Repeating the same request returns the same mission and never creates another branch.

```bash
agent-worktrees register \
  --db /tmp/agent-worktrees.sqlite3 \
  --repo . \
  --worktree-root ../my-project-agent-worktrees \
  --request examples/mission.json
```

Provision the returned mission ID:

```bash
agent-worktrees provision \
  --db /tmp/agent-worktrees.sqlite3 \
  --mission MISSION_ID \
  --actor scheduler
```

After the assigned agent commits only inside its declared ownership and creates the declared artifact, replace the SHA in `examples/evidence.json` with the exact worktree `HEAD`:

```bash
agent-worktrees complete \
  --db /tmp/agent-worktrees.sqlite3 \
  --mission MISSION_ID \
  --actor docs-agent \
  --evidence examples/evidence.json
```

Merge the mission branch using your normal reviewed integration flow. Cleanup is rejected until Git proves the branch is an ancestor of its base ref. For a squash-equivalent owned tree, pass `--keep-branch`; the tool will not force-delete a branch Git considers unmerged:

```bash
agent-worktrees cleanup \
  --db /tmp/agent-worktrees.sqlite3 \
  --mission MISSION_ID \
  --actor integrator
```

## Ownership contract

Paths are repository-relative and normalized to POSIX separators. Absolute paths, parent traversal, the repository root, and `.git` are rejected. Two active missions conflict when either declared path is equal to or nested under the other. Sibling scopes such as `src/api` and `src/web` remain independent.

Ownership stays reserved through failure and completion until safe cleanup succeeds. This prevents another worker from entering a scope while unintegrated commits or a retry may still exist.

## Completion contract

`done` requires all of the following:

1. the registered worktree still points to the mission branch;
2. `git status --porcelain --untracked-files=all` is empty;
3. the evidence commit equals the worktree `HEAD`;
4. the evidence producer equals the assigned agent;
5. every committed path is inside declared ownership;
6. every listed artifact is a real file inside the worktree;
7. every declared acceptance criterion exists and is `true`;
8. at least one test result and artifact are present.

Text saying "done" is never accepted as proof.

## Commands

| Command | Purpose |
| --- | --- |
| `register` | Idempotently reserve ownership and create a queued mission |
| `provision` | Create or resume the deterministic branch and linked worktree |
| `wait` / `resume` | Keep blockers and escalations visible |
| `fail` / `retry` | Record failure and reuse the same Git resources inside the retry budget |
| `intervene` | Record an explicit human action |
| `complete` | Validate Git state and the evidence bundle before `done` |
| `cleanup` | Remove only clean, integrated work; optionally retain the merged branch |
| `recover` | Convert missing or mismatched active worktrees into visible failures |
| `audit` | Compare the durable registry with `git worktree list --porcelain` |
| `inspect` / `list` | Return mission state and ordered events as JSON |
| `metrics` | Return durable operational measures |
| `demo` | Run the complete disposable journey without an account or remote |

Successful commands emit JSON to stdout. Exit code `2` is invalid input, `3` is an unknown mission, and `4` is a rejected Git, safety, or state operation. JSON inputs are limited to 1 MB.

## Verification

```bash
python scripts/check.py
python -m unittest discover -s tests -v
agent-worktrees demo --workspace /tmp/agent-worktrees-demo
python -m pip wheel . --no-deps --wheel-dir /tmp/wheel
```

See [Architecture](docs/ARCHITECTURE.md), [Safety model](docs/SAFETY.md), [SQLite schema](docs/SCHEMA.md), [Security policy](SECURITY.md), and [AI assistance disclosure](AI_ASSISTANCE.md).

## License

Apache-2.0.
