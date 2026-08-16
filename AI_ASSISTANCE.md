# AI assistance disclosure

This repository includes material AI-assisted work. Assistance was used to propose and implement portions of the architecture, strict models, SQLite schema, transactional engine, CLI, local read-only dashboard, synthetic examples, tests, and documentation.

The accepted design was constrained by explicit human-directed requirements: dependency-free Python, no arbitrary execution, deny-by-default governance, immutable definitions, bound approvals, bounded budgets/retries/deadlines, durable recovery, public compatibility, synthetic data, and no external services or secrets.

AI output is not an authority, provenance guarantee, vulnerability assessment, or security attestation. Maintainers are responsible for reviewing every change, understanding trust boundaries, running fresh tests, evaluating deployment-specific risks, and rejecting suggestions that weaken invariants.

Material AI-assisted changes should continue to disclose:

- what surfaces were assisted;
- which requirements and threat assumptions constrained the work;
- which tests and independent checks were run;
- what remains unverified or deployment-dependent.

No external private source, production dataset, credential, client record, or network service is required by this package or its tests. All examples and test fixtures are synthetic.

