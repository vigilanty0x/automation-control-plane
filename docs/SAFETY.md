# Safety model

## Refuse by default

The service rejects:

- a path outside the repository, parent traversal, repository-root ownership, or `.git` ownership;
- a mission whose scope equals, contains, or is contained by an active mission scope;
- a worktree path already used by another branch;
- a branch already checked out in another worktree;
- completion from a dirty, missing, or mismatched worktree;
- completion when committed paths exceed ownership;
- an evidence producer different from the assigned agent;
- an artifact that is missing or escapes the worktree;
- cleanup before `done`, before integration, or while the worktree is dirty;
- retry after the declared attempt budget.

## No destructive fallback

Cleanup never calls `rm`, `git worktree remove --force`, or `git branch -D`. If Git cannot prove the work is clean and merged, the resources remain in place and the command returns a bounded error.

## Remaining responsibilities

This tool is not an authorization server or sandbox. A production wrapper should authenticate callers, protect the SQLite file, restrict who may register or complete missions, independently verify test references, and back up local repositories. Review and merge policies remain outside this package.
