# Threat model

## Assets

- minimized local observations and their profile association;
- human answers and procedural memory;
- integrity/provenance of routines, Skill IR, and LearnPacks;
- API bearer/session/bootstrap secrets;
- user trust that preview never executes.

## Trust boundaries

Untrusted boundaries are JSONL files, HTTP bodies/headers/paths, LearnPack archives, CLI files, and all user-supplied strings. SQLite and in-process objects are trusted only after validation and remain profile-scoped. The operating-system account and Python runtime are assumed uncompromised.

## Material threats and controls

| Threat | Controls | Residual risk |
|---|---|---|
| Secret/PII persistence | closed fields, semantic/pattern redaction, raw DB/WAL tests | unknown encodings or novel secret formats |
| Event deletion/mutation | chained canonical digests plus incremental/final anchors | privileged full-history rewrite |
| Cross-profile IDOR | profile predicates and relationship checks | bugs in future queries |
| Forged learning evidence | store recomputation, exact D1–D5 split, answer/memory linkage | only reference template covered |
| Host/CSRF/browser attack | loopback bind, exact Host/Origin, one-use ticket, distinct HttpOnly cookie, CSP | hostile process under same OS account |
| Retry duplication | durable reservation, request digest, replay/conflict state | crash leaves explicit pending/unknown state |
| ZIP traversal/bomb/swap | one descriptor snapshot, size/count/ratio/type/name/digest checks, no extraction | parser vulnerabilities in Python runtime |
| Stale authority | invalidation propagation, active-only listing, store-revalidated preview/export | callers bypassing public interfaces |
| Accidental execution | no executor, shell, subprocess, network or external effects | downstream forks adding unsafe execution |

## Abuse cases explicitly tested

Tail deletion, forged confirmed routines, fake holdouts, duplicate IDs, cross-profile relationships, secret-bearing metadata/audit/idempotency/preview values, symlinks, JSON depth/numeric attacks, ZIP traversal/symlinks/bombs/digest mismatch/TOCTOU, DNS rebinding Host values, CSRF origins, oversized/pipelined HTTP bodies, and repeated mutations.
