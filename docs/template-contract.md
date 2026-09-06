# Reusable native templates

This is a compiler into the existing FactorySpec and a provenance link in the
existing Store journal. There is one Engine, one queue, and one authority for
claims, approval, quotas, receipts and interruption. Compilation alone does not
run a task, inspect Git, or prove software quality.

## Input and compilation

A catalog is exactly `{"format":"ai-software-factory/template-catalog-v1",
"templates":{"template-id":{...native FactorySpec...}}}`. IDs match
`[a-z][a-z0-9_-]{0,63}`; the selected ID must exist in the supplied catalog.
There is no implicit discovery, remote loading or plugin execution.

Limits: 1 MiB raw and normalized catalog; 1..32 templates; at most 1000 tasks
and 100 distinct variables per template. Bindings are an object containing
exactly the used variables, at most 64 KiB in total. Values are strings of at
most 4096 UTF-8 bytes. JSON has at most 32 nested levels and 200000 nodes;
duplicate keys, unsupported types, non-finite numbers (including 1e309),
invalid Unicode and recursive placeholder values are refused.

Catalog and bindings use a separate file reader: one pass consumes at most the
byte limit plus one, before parsing. Only regular single-link files are accepted;
ancestors and leaf may not be symbolic links or Windows reparse points. POSIX
opens are anchored with O_NOFOLLOW. Windows uses CreateFileW OPEN_EXISTING with
OPEN_REPARSE_POINT, a local drive only, and FileBasicInfo ChangeTime checks.
File/descriptor/path identities are checked before and after reading; growth,
replacement and mutation are refusals. Legacy CLI file loading is unchanged.

Placeholders use `{{UPPERCASE_NAME}}`. They are allowed only in the spec name,
task description, and values of environment variables named `FACTORY_INPUT_*`.
Substitution is one pass with no expression evaluation. Environment keys, other
environment values, commands/test arguments, workspace/owned/artifact paths,
owners, dependencies, task IDs, timeouts, quotas and approval policy are fixed.
The full catalog and resulting spec undergo native DAG/ownership/policy checks.
Malformed or unsupported placeholders are refused, including in unused models.
On a template run, Provider task and test requests must exactly match SpecProvider
before dispatch. The effective spec and validated origin marker are read from
one Store snapshot, including after reopening the database. This restriction
does not change dynamic Provider support on native runs without a template,
approval requirement, or execution quota.

This deliberately narrows workflow-templates' generic substitution: its source
accepts other scalar types and other non-ID/dependency fields. Factory does not
substitute into a command or PATH/PYTHONPATH/loader environment variable.
The catalog itself remains trusted operator-authored code, exactly like a
native specification. A malicious fixed program can interpret data as code;
this compiler is not an OS sandbox and does not make such programs safe.

## Native API and commands

`compile_template(catalog, template_id, bindings)` returns `Compilation` with
`spec`, `origin` and `to_dict()`. It performs no I/O. JSON entry points use
`read_json(source, maximum=...)` for duplicate/size/type checks.

`FactoryEngine.plan_template(...)` validates first, then uses the native
workspace and `FactoryStore.create_run(..., template_origin=...)`. Execute or
resume the returned run ID with the unchanged `FactoryEngine.run`. It uses
the same Provider/Executor rules, attempts, waits and stop behavior.

```bash
ai-software-factory template-compile catalog.json --template component-v1 --bindings bindings.json --output compiled.json
ai-software-factory template-plan catalog.json --template component-v1 --bindings bindings.json --db state.sqlite3
ai-software-factory template-run catalog.json --template component-v1 --bindings bindings.json --db state.sqlite3
```

Compile emits the effective spec and origin; output files are create-only.
Plan/run use the same JSON status and exit semantics as native commands:
0 for a successful operation, 2 for failure, and 3 for explicit approval/quota
waiting. No database or workspace is created for an invalid catalog/binding.
The existing `status`, `replay`, `export`, `verify`, `kill`, and approval CLI
commands inspect/control these runs too. There is no template-specific queue.

## Provenance and replay

The origin format is `ai-software-factory/template-origin-v1`, compiler
`nonstructural-v1`. It retains the normalized selected template, bindings,
their SHA-256 values and effective spec SHA-256. It explicitly records
`caller_declared_template_recompiled_locally`; there is no authenticated author,
upstream signature, Git commit attestation or independent review implied.

The native `run.created` event anchors the origin digest. The immediately
following `run.template_compiled` event contains the full origin. Both are
committed in the same transaction as the spec and tasks. Failure rolls back all
of these rows. The default idempotency key binds the origin; an explicit key
cannot be reused for another origin even if the compiled spec happens to match.

Store replay, spec loading before execution, and offline export verification
recompile the retained inputs and compare them with the effective spec. Missing,
duplicate, reordered, inconsistent or unsupported origins fail closed. Merely
recomputing a top-level digest is insufficient. As for the existing journal,
someone able to rewrite all records and all anchors is not authenticated by
these hashes; this is reproducible consistency, not a signature.

Without template provenance, spec serialization and receipt/export structures
remain unchanged. Database schema stays at v3. Template source and bindings
are persistent run data, so they must contain no credentials or secrets.

## Scope still open

No Git worktree provisioning, branch mutation, worktree cleanup or merge occurs.
No new cross-run/cross-Store workspace exclusion is claimed. Use distinct base
directories/workspaces for independent or concurrent projects; changing only
bindings does not allocate a new workspace. Task leases protect a run within
the same Store, and the native ownership rules protect paths within its DAG.

Historical starter-kit review/release declarations are not converted into
measured tests or authenticated approvals. Worktree Conflict Visualizer remains
a declared-path analyzer, not a merge-conflict probe. Markdown handoff generation
is not integrated by this compiler. Their remaining capabilities stay open.

Template normalization/substitution principle: workflow-templates commit
`f210986b2fa7917c1b70ce0f82b10f23e87f63b7`, Apache-2.0. Native execution,
artifacts and export verification belong to AI Software Factory.
