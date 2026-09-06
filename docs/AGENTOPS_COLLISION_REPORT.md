# AgentOps collision and deduplication report

## Decision

Use `automation-control-plane` as the prepared base. Add bounded AgentOps seams inside that package. Do not create another repository during the active freeze.

## Collision matrix

| Source concept | Existing overlap | Rehearsal disposition | Reason |
| --- | --- | --- | --- |
| `agentmesh` | No detailed route graph in core | Add strict `routing_evidence` seam | Preserve route health and ownership evidence without importing dead generic branches |
| `agent-budgeter` | Core already reserves and settles integer job/step budgets atomically | Deduplicate into core | A second budget authority could disagree with execution |
| `agent-inbox` | Core already owns durable jobs, leases, approvals, retries, and state | Add read-only `operator_inbox` projection only | Avoid a second durable mission queue and split-brain state |
| `agent-quota-simulator` | Core budgets actual controlled execution, not provider planning | Add simulation-only test lab | Keep planning explicitly separate from enforcement |
| `agent-retry-kit` | Core already enforces bounded retries and deterministic backoff | Deduplicate into core | One retry authority per job |
| `agent-session-recorder` | Core audit chain covers control-plane events, not arbitrary session evidence | Add redaction-aware `session_evidence` seam | Keep generic evidence separate and distinguish integrity from authenticity |
| `circuit-breaker-lab` | Core kill switches are operator controls, not circuit simulations | Add simulation-only test lab | Do not conflate emergency stop with service circuit state |
| `context-window-budgeter` | No context-window planner in core | Add pure `context_budgets` seam | Bounded planning has no execution side effect |
| `human-in-the-loop-queue` | Core already has durable digest-bound approvals and RBAC | Deduplicate into core | Avoid weaker parallel approval semantics |
| `idempotency-kit` | Core binds idempotency to the complete canonical submission | Deduplicate into core | Preserve the stronger durable contract |
| `taskgraph` | Core already persists workflows, DAG dependencies, claims, and recovery | Deduplicate into core | Avoid two task state machines |
| `timeout-toolkit` | Core already owns deadlines and lease expiry | Deduplicate into core | One authoritative clock/deadline path |
| `automation-control-plane` | Complete durable execution authority | Selected base | Strongest existing safety and recovery boundary |

## Source import status

This change is a contract rehearsal, not a history import. It implements reviewed public behaviors behind new nested modules and records exact source HEADs. It does not copy repository histories, close source pull requests, redirect users, or archive sources.

## Counter-proof requirement

A future migration must fail if it introduces any of the following:

- a second durable job, approval, retry, budget, idempotency, or deadline authority;
- arbitrary command execution or executable workflow content;
- route ownership that does not match the target owner;
- a source archive without release, compatibility, consumer, redirect, rollback, and named human gates;
- a claim that a simulation reserves real provider quota or enforces runtime state.
