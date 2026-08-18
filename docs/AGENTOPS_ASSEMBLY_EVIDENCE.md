# AgentOps assembly evidence

The one-time rehearsal assembly was bound to the following immutable inputs:

- reviewed parent: `dce29d262fc068cb9fff05ec55d8926316a3ba84`;
- assembled tree commit: `5bff6183f802c14ce00536a5228dda903322c864`;
- archive SHA-256: `d77bf49483269772377883fb1ac6d4e679c18d01f5a0aa2c89fee4ca49ba4195`;
- manifest entries: 23 files, each with an expected byte count and SHA-256 digest.

The temporary workflow verified the archive digest, exact membership, every file digest, path containment, and regular-file type before writing the tree. It then ran bytecode compilation, the complete repository unit suite, the repository checker, and every AgentOps example command before the commit step was reachable.

The resulting commit removed the temporary bootstrap directory and temporary workflow. Their absence is part of the review evidence: the product branch contains only the bounded AgentOps source, tests, examples, and documentation.

This document records **PREPARED** rehearsal evidence only. It does not claim a merge, release, consumer migration, redirect, rollback rehearsal, source-history import, or source archive. Those remain separately gated and require named human approval where specified by the consolidation policy.
