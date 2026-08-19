from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

MAX_INPUT_BYTES = 2_000_000
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PacketError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise PacketError(f"missing input file: {path}")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise PacketError(f"input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketError(f"input must be UTF-8: {path}") from exc
    try:
        value = json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                PacketError(f"non-standard JSON constant in {path}: {token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise PacketError(f"invalid JSON in {path}: line {exc.lineno}") from exc
    if type(value) is not dict:
        raise PacketError(f"input must contain a JSON object: {path}")
    return value, sha256(raw).hexdigest()


def _expect_sha256(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise PacketError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _proof_summary(
    role: str,
    value: dict[str, Any],
    raw_sha256: str,
    *,
    adapter_guardrails: bool = False,
) -> dict[str, Any]:
    if value.get("status") != "passed":
        raise PacketError(f"{role} proof is not passed")
    evidence_sha256 = _expect_sha256(value.get("evidence_sha256"), f"{role}.evidence_sha256")
    for field in ("migration_performed", "legacy_aliases_activated"):
        if value.get(field) is not False:
            raise PacketError(f"{role}.{field} must be false")
    if adapter_guardrails:
        for field in ("consumer_mutation_performed", "source_retirement_authorized"):
            if value.get(field) is not False:
                raise PacketError(f"{role}.{field} must be false")
    return {
        "status": "passed",
        "evidence_sha256": evidence_sha256,
        "raw_file_sha256": raw_sha256,
    }


def _migration_summary(value: dict[str, Any], raw_sha256: str) -> dict[str, Any]:
    if value.get("status") != "passed":
        raise PacketError("migration plan is not passed for technical planning")
    details = value.get("details")
    if type(details) is not dict:
        raise PacketError("migration plan details are missing")
    if details.get("planning_evidence_ready") is not True:
        raise PacketError("migration plan is not technically ready")
    if details.get("formal_migration_gate") != "blocked":
        raise PacketError("formal migration gate must remain blocked")
    if details.get("legacy_aliases_activated") is not False:
        raise PacketError("legacy aliases must remain disabled")
    if details.get("migration_performed") is not False:
        raise PacketError("migration must not already be performed")
    if details.get("irreversible_actions_allowed") is not False:
        raise PacketError("irreversible actions must remain disallowed")
    if details.get("named_human_approval_required") is not True:
        raise PacketError("named human approval requirement must remain explicit")
    if details.get("default_branch_live_evidence_required") is not True:
        raise PacketError("default-branch live evidence requirement must remain explicit")
    evidence_sha256 = _expect_sha256(value.get("evidence_sha256"), "migration_plan.evidence_sha256")
    return {
        "status": "passed",
        "evidence_sha256": evidence_sha256,
        "raw_file_sha256": raw_sha256,
        "planning_evidence_ready": True,
        "pilot_coverage_complete": details.get("pilot_coverage_complete") is True,
        "observed_runtime_reference_count": details.get("observed_runtime_reference_count"),
        "adapter_candidates": details.get("adapter_candidates", []),
    }


def build_packet(
    *,
    candidate_sha: str,
    migration_plan: dict[str, Any],
    migration_plan_raw_sha256: str,
    compatibility: dict[str, Any],
    compatibility_raw_sha256: str,
    adapters: dict[str, Any],
    adapters_raw_sha256: str,
    core: dict[str, Any],
    core_raw_sha256: str,
) -> dict[str, Any]:
    if not _GIT_SHA.fullmatch(candidate_sha):
        raise PacketError("candidate_sha must be a lowercase 40-character Git SHA")

    migration = _migration_summary(migration_plan, migration_plan_raw_sha256)
    proofs = {
        "compatibility": _proof_summary(
            "compatibility", compatibility, compatibility_raw_sha256
        ),
        "adapters": _proof_summary(
            "adapters", adapters, adapters_raw_sha256, adapter_guardrails=True
        ),
        "core": _proof_summary("core", core, core_raw_sha256),
    }

    human_inputs_required = [
        "default_branch_live_evidence",
        "named_human_approval",
    ]
    if not migration["pilot_coverage_complete"]:
        human_inputs_required.insert(0, "pilot_adopter_completeness_attestation")

    packet: dict[str, Any] = {
        "schema_version": 1,
        "kind": "agentops_technical_review_packet",
        "status": "passed",
        "candidate_sha": candidate_sha,
        "technical_readiness": True,
        "migration_plan": migration,
        "proofs": proofs,
        "human_inputs_required": human_inputs_required,
        "formal_migration_gate": "blocked",
        "alias_activation_authorized": False,
        "consumer_mutation_authorized": False,
        "migration_authorized": False,
        "release_authorized": False,
        "source_retirement_authorized": False,
        "archive_authorized": False,
        "rule": (
            "this packet binds technical evidence only; it cannot authorize migration, "
            "aliases, consumer mutation, release, source retirement, or archive"
        ),
    }
    packet["evidence_sha256"] = sha256(_canonical(packet).encode("utf-8")).hexdigest()
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a deterministic fail-closed AgentOps technical review packet."
    )
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--migration-plan", required=True)
    parser.add_argument("--compatibility", required=True)
    parser.add_argument("--adapters", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        migration_plan, migration_digest = _read_json(Path(args.migration_plan))
        compatibility, compatibility_digest = _read_json(Path(args.compatibility))
        adapters, adapters_digest = _read_json(Path(args.adapters))
        core, core_digest = _read_json(Path(args.core))
        packet = build_packet(
            candidate_sha=args.candidate_sha,
            migration_plan=migration_plan,
            migration_plan_raw_sha256=migration_digest,
            compatibility=compatibility,
            compatibility_raw_sha256=compatibility_digest,
            adapters=adapters,
            adapters_raw_sha256=adapters_digest,
            core=core,
            core_raw_sha256=core_digest,
        )
        rendered = json.dumps(packet, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        Path(args.output).write_text(rendered, encoding="utf-8", newline="\n")
        print(
            _canonical(
                {
                    "status": packet["status"],
                    "technical_readiness": packet["technical_readiness"],
                    "formal_migration_gate": packet["formal_migration_gate"],
                    "human_inputs_required": packet["human_inputs_required"],
                    "evidence_sha256": packet["evidence_sha256"],
                }
            )
        )
        return 0
    except (OSError, PacketError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
