# Architecture

## Components

`MissionRequest` defines the agent, human owner, base ref, exclusive paths, measurable criteria, retry budget, and idempotency key. `SQLiteMissionStore` owns durable state, conflict detection, retries, interventions, events, and metrics. `AgentWorktreeService` is the only layer that executes Git.

Git commands use a fixed executable and argument arrays through `subprocess.run`; no user value is interpolated into a shell command. Every command has captured output and a 30-second timeout.

## Lifecycle

1. `register` resolves the canonical repository root and base commit.
2. The store returns an existing identical idempotency key or reserves non-overlapping ownership.
3. `provision` creates `agent/<agent>/<task>-<key-hash>` and one linked worktree.
4. An existing matching worktree is resumed instead of duplicated.
5. State changes append durable events.
6. `complete` verifies the clean worktree, exact commit, owned diff, artifacts, producer, tests, and criteria.
7. The normal reviewed integration process merges the branch.
8. `cleanup` accepts an ancestor merge or an identical owned tree after a squash. It removes the worktree without force and uses `git branch -d` only for ancestor merges. A squash-equivalent branch requires explicit retention with `--keep-branch`.

## Idempotency and recovery

The idempotency fingerprint includes the complete mission request, canonical repository root, and worktree root. Reusing a key for different work is rejected. Retrying increments the attempt on the same row; its branch and worktree path never change.

`recover` checks every active mission against Git's porcelain worktree registry. A missing or branch-mismatched entry becomes `failed` once. The event explains why, and a bounded retry can restore the original branch into the original path.

## Boundaries

The project manages local Git metadata and directories that Git itself registered as linked worktrees. It does not merge, push, open pull requests, contact hosting services, run agent code, or judge test output. Integration remains an external reviewed action; test strings are evidence references supplied by the caller.
