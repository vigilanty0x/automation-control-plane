# Automation Control Plane

## Purpose

Governed offline automation state transitions, approvals, kill switches, and budgets. The package is standard-library-only and designed for deterministic local use with synthetic or caller-controlled JSON.

## Non-goals

It does not execute jobs, authenticate principals, persist state, or issue capabilities.

## Install

Requires Python 3.11 or newer.

```bash
python -m pip install .
```

## CLI and API

Pass a JSON object by path or standard input. Success is emitted as machine-readable JSON; validation failures return exit status 2 without a traceback.

```bash
automation-control-plane examples/basic.json
python -m automation_control_plane.cli examples/basic.json
```

The JSON-facing public API is `automation_control_plane.core.run(data)`. It accepts only a job and target and returns an explicitly unverified simulation. Authorized state changes use the separate programmatic `transition(...)` boundary with identity, capabilities, trusted current state, and any approval supplied by trusted application code.

## Example

The example simulates whether one synthetic pending job could enter approval; it does not authorize or mutate the job.

```bash
automation-control-plane examples/basic.json
```

All example content is synthetic and safe to publish.

## Security and trust model

The CLI never accepts principal, capabilities, approvals, current state attestations, or kill-switch authority and never labels its result authorized. Trusted current state, principal, and capabilities enter only through the programmatic transition boundary. Approvals bind job ID, version, action, and approver; kill switches require a kill capability; budgets use strict finite bounds.

The caller remains responsible for authenticating inputs and enforcing returned decisions at the real I/O or authorization boundary. Invalid and inconclusive inputs fail visibly rather than producing a healthy or verified claim.

## Limitations

CLI output is simulation-only. Programmatic callers must obtain current state, identity, capabilities, and approvals from trusted systems rather than request JSON, then persist returned versions atomically.

## Tests

Run the full local contract:

```bash
python -m unittest discover -s tests -v
python scripts/check.py
python -m build --no-isolation
```

CI exercises Python 3.11 and 3.12, builds and installs the wheel, then runs tests, the public-boundary check, the module example, and the installed console command.

## AI assistance

AI-assisted contribution details and validation expectations are documented in [AI_ASSISTANCE.md](AI_ASSISTANCE.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
