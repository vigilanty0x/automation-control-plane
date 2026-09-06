from __future__ import annotations

from typing import Any

from ._common import evidence
from .compatibility import SOURCE_INTERFACES

CONTRACT_SCHEMA_VERSION = "1.0"

# These records are planning contracts only. They describe the minimum semantic
# boundary an eventual adapter must preserve. They do not activate package,
# import, or CLI aliases and they perform no migration.
MIGRATION_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "repository": "agentmesh",
        "source_sha": "320f5116f6582519d1609ce87287fd9ff7267eb3",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "candidate_adapter",
        "source_surface": "count-oriented agent routing and health evidence",
        "target_surface": "agentops route identity/owner/capability/health evidence",
        "preserved_invariants": ["healthy eligible route remains eligible after explicit identity mapping"],
        "semantic_deltas": ["source count-oriented schema is not a target identity/ownership route schema"],
        "adapter_requirements": ["caller must provide explicit target owner and capability identity; never infer them from repository names"],
        "activation_state": "blocked",
    },
    {
        "repository": "context-window-budgeter",
        "source_sha": "35bb3e05d05ad870715b740143c429f08eda25e7",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "candidate_adapter",
        "source_surface": "required-first context selection with source tie-break ordering",
        "target_surface": "agentops context required-first planning with output reservation",
        "preserved_invariants": ["required overflow remains fail-closed", "required items precede optional selection"],
        "semantic_deltas": ["equal-priority optional tie-break ordering differs"],
        "adapter_requirements": ["adapter must preserve the source tie-break explicitly or expose a versioned ordering choice; silent reorder is forbidden"],
        "activation_state": "blocked",
    },
    {
        "repository": "agent-quota-simulator",
        "source_sha": "e99000cecf12432365e8ccfc8fa6e4b1d18ad15f",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "candidate_adapter",
        "source_surface": "budget/seconds/cost_micros quota selection",
        "target_surface": "agentops quota token/time_ms/micro_cost planning with required-task semantics",
        "preserved_invariants": ["invalid budgets remain rejected", "admitted work stays within declared resource limits"],
        "semantic_deltas": ["resource field names and units differ", "source has no required-task concept"],
        "adapter_requirements": ["unit conversion must be exact and bounded", "required-task behavior must be an explicit target-only extension, never retroactively attributed to the source"],
        "activation_state": "blocked",
    },
    {
        "repository": "agent-session-recorder",
        "source_sha": "2363c4efe0c61158c523a6dfc3d29cb3d7af1c54",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "candidate_adapter",
        "source_surface": "legacy session record/hash evidence",
        "target_surface": "agentops session evidence with redaction-aware timestamp/actor/session hash chain",
        "preserved_invariants": ["record tampering remains detectable", "sequence ordering remains validated"],
        "semantic_deltas": ["target rejects sensitive key names accepted by the source", "target receipts bind richer session semantics"],
        "adapter_requirements": ["sensitive fields must be rejected or explicitly transformed before target validation", "adapter must not claim source receipts are target authenticity proofs"],
        "activation_state": "blocked",
    },
    {
        "repository": "circuit-breaker-lab",
        "source_sha": "2924dfb6eed8a208788491fa1d50fa6bd99e4359",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "candidate_adapter",
        "source_surface": "wall-clock closed/open/half-open circuit behavior",
        "target_surface": "agentops circuit explicit-event simulation",
        "preserved_invariants": ["failure threshold opens the circuit"],
        "semantic_deltas": ["source silently waits through wall-clock cooldown while target requires an explicit cooldown_elapsed event"],
        "adapter_requirements": ["adapter may translate an externally observed cooldown into an explicit event only when that observation is supplied as evidence; it must never fabricate elapsed time"],
        "activation_state": "blocked",
    },
    {
        "repository": "agent-inbox",
        "source_sha": "748f237659f98a2a49478aa58913e71e59a03433",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "projection_only",
        "source_surface": "durable mutating SQLite mission queue",
        "target_surface": "agentops inbox read-only operator projection",
        "preserved_invariants": ["queued-state visibility remains deterministic"],
        "semantic_deltas": ["source owns durable queue mutation while target projection intentionally performs no mutation"],
        "adapter_requirements": ["read-only projection may consume exported state", "enqueue/dequeue/transition semantics remain owned by the durable control plane or the source until separately migrated"],
        "activation_state": "blocked",
    },
    {
        "repository": "agent-budgeter",
        "source_sha": "cfa9c0a8830f3e2e3a11602da590e347f8483d2f",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "incompatibility_contract",
        "source_surface": "calls/time_ms/tokens BudgetVector",
        "target_surface": "durable workflow budget_units plus separate timeout and retry controls",
        "preserved_invariants": ["budgets remain bounded and non-negative"],
        "semantic_deltas": ["source and target budget dimensions are not losslessly equivalent", "source accepts bool through Python int semantics while target rejects bool"],
        "adapter_requirements": ["no drop-in alias", "any future mapping requires a declared dimensional policy and counter-proof suite before activation"],
        "activation_state": "blocked",
    },
    {
        "repository": "agent-retry-kit",
        "source_sha": "38f97aaa6796fda6956202aad9086e3e5e8ada9f",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "incompatibility_contract",
        "source_surface": "retryable error taxonomy with millisecond exponential backoff",
        "target_surface": "durable RetryPolicy integer-second schedule without source error taxonomy",
        "preserved_invariants": ["retry attempts remain bounded"],
        "semantic_deltas": ["error-classification semantics are absent from target RetryPolicy", "delay resolution differs from milliseconds to integer seconds"],
        "adapter_requirements": ["no implicit quantization or error-class loss", "a future adapter must version both taxonomy mapping and rounding policy"],
        "activation_state": "blocked",
    },
    {
        "repository": "human-in-the-loop-queue",
        "source_sha": "db281bf7ff971a2fd4ca6af9e495b2c0fd6cd30b",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "evidence_only",
        "source_surface": "approved/rejected queue record with expiry and audit evidence",
        "target_surface": "durable approval bound to job version, action, principal, and capability",
        "preserved_invariants": ["expired approvals are not treated as current decisions"],
        "semantic_deltas": ["source approval record lacks target authorization bindings"],
        "adapter_requirements": ["source records may be imported only as historical evidence", "they must never authorize a durable target transition without a new bound target approval"],
        "activation_state": "blocked",
    },
    {
        "repository": "idempotency-kit",
        "source_sha": "80e78a4d9b73aeabb200578c2ecb5090f31a410f",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "evidence_only",
        "source_surface": "request_id plus caller-provided fingerprint validation",
        "target_surface": "durable idempotency key bound to the complete canonical submission request",
        "preserved_invariants": ["re-use of an idempotency identity remains detectable"],
        "semantic_deltas": ["source rule does not bind changed result content while target binds complete canonical request semantics"],
        "adapter_requirements": ["source receipts may be retained as evidence only", "target idempotency decisions must be recomputed from the target canonical request"],
        "activation_state": "blocked",
    },
    {
        "repository": "taskgraph",
        "source_sha": "937abf0d096cb1f6ff48aa09b8dd8a69d9a36c0d",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "incompatibility_contract",
        "source_surface": "task DAG with path ownership and required evidence kinds",
        "target_surface": "durable workflow DAG with handler/capability/approval execution semantics",
        "preserved_invariants": ["dependency cycles remain rejected"],
        "semantic_deltas": ["target workflow schema has no path-ownership field", "source required-evidence semantics are not target handler/capability semantics"],
        "adapter_requirements": ["path ownership and required evidence must remain a separate validated contract if imported", "no source taskgraph may be represented as equivalent solely because its dependency DAG parses"],
        "activation_state": "blocked",
    },
    {
        "repository": "timeout-toolkit",
        "source_sha": "a2a053e39a6eaa40cde7d81f87ee4838cf562583",
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "strategy": "evidence_only",
        "source_surface": "external elapsed_ms versus timeout_ms attestation",
        "target_surface": "durable integer-second execution deadline and lease/recovery enforcement",
        "preserved_invariants": ["bounded timeout evidence remains fail-closed on invalid inputs"],
        "semantic_deltas": ["source accepts floating-point milliseconds while target rejects floating-point timeout values", "elapsed-time evidence is not execution-deadline enforcement"],
        "adapter_requirements": ["source receipts may be attached as external evidence only", "they must not be converted into target deadline enforcement claims"],
        "activation_state": "blocked",
    },
)


def migration_contract_inventory() -> dict[str, Any]:
    source_interfaces = {
        item["repository"]: item["source_sha"]
        for item in SOURCE_INTERFACES
        if item["repository"] != "automation-control-plane"
    }
    contract_sources = {item["repository"]: item["source_sha"] for item in MIGRATION_CONTRACTS}
    exact_source_match = source_interfaces == contract_sources
    safe_states = all(
        item["activation_state"] == "blocked"
        and item["strategy"] in {"candidate_adapter", "projection_only", "evidence_only", "incompatibility_contract"}
        and bool(item["semantic_deltas"])
        and bool(item["adapter_requirements"])
        for item in MIGRATION_CONTRACTS
    )
    payload = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contracts": list(MIGRATION_CONTRACTS),
        "exact_source_match": exact_source_match,
    }
    details = {
        **payload,
        "contract_count": len(MIGRATION_CONTRACTS),
        "legacy_aliases_activated": False,
        "migration_performed": False,
        "irreversible_actions_allowed": False,
        "consumer_inventory_required_before_activation": True,
        "human_approval_required_before_activation": True,
        "rule": "contracts may narrow future adapter behavior but cannot activate aliases, migrate consumers, or authorize source retirement",
    }
    return evidence(
        "migration_contract_inventory",
        "passed" if exact_source_match and safe_states and len(MIGRATION_CONTRACTS) == 12 else "failed",
        payload,
        details,
    )
