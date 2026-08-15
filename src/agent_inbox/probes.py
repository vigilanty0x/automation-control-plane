"""Offline liveness, readiness, persistence, and fail-closed counter-proofs."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .contract import (
    AgentProfile, ArtifactEvidence, CompletionEvidence, MissionSpec, TestEvidence,
)
from .inbox import AgentInbox, EvidenceRequired, NoMissionAvailable


class ProbeClock:
    def __init__(self, value: float = 1_700_000_000.0) -> None: self.value = value
    def __call__(self) -> float: return self.value


def liveness() -> dict[str, object]:
    return {"probe": "liveness", "ok": True, "contract": "1.0"}


def readiness(path: str | Path) -> dict[str, object]:
    try:
        inbox = AgentInbox(path); inbox.initialize(); inventory = inbox.inventory()
    except (OSError, RuntimeError) as exc:
        return {"probe": "readiness", "ok": False, "reason": type(exc).__name__}
    return {"probe": "readiness", "ok": inventory["schema_version"] == "1.0", "database": "ready"}


def functional_counter_proof() -> dict[str, object]:
    with TemporaryDirectory(prefix="agent-inbox-probe-") as directory:
        clock = ProbeClock(); tokens = iter(("lease-a", "lease-b", "lease-c"))
        path = Path(directory) / "inbox.sqlite3"
        inbox = AgentInbox(path, clock=clock, token_factory=lambda: next(tokens))
        inbox.register_agent(AgentProfile("worker", ("python",), ("write",), ("demo",), max_lease_seconds=5))
        spec = MissionSpec("mission-1", "Synthetic mission", {"fixture": True}, 80, "demo", ("python",), ("write",), 1)
        first = inbox.enqueue(spec); replay = inbox.enqueue(spec)
        claim = inbox.claim("worker", lease_seconds=5)
        duplicate_prevented = False
        try: inbox.claim("worker", lease_seconds=5)
        except NoMissionAvailable: duplicate_prevented = True
        evidence_blocked = False
        try: inbox.complete(first["mission_id"], claim["lease_token"], CompletionEvidence("No proof"))
        except EvidenceRequired: evidence_blocked = True
        inbox.record_signal(first["mission_id"], event_id="disagree-1", kind="disagreement", actor="reviewer", detail={"reason": "synthetic disagreement"})
        inbox.record_signal(first["mission_id"], event_id="escalate-1", kind="escalation", actor="reviewer", detail={"to": "maintainer"})
        evidence = CompletionEvidence(
            "Synthetic completion",
            tests=(TestEvidence("unit", "passed", "python -m unittest"),),
            artifacts=(ArtifactEvidence("report", "a" * 64, "artifacts/report.json"),),
        )
        done = inbox.complete(first["mission_id"], claim["lease_token"], evidence)

        second = inbox.enqueue(MissionSpec("mission-2", "Lease recovery", {"fixture": 2}, 70, "demo", ("python",), ("write",), 1))
        inbox.claim("worker", lease_seconds=5); clock.value += 6
        recovered = inbox.recover_expired(); second_claim = inbox.claim("worker", lease_seconds=5)
        clock.value += 6; exhausted = inbox.recover_expired()
        persisted = AgentInbox(path).get(first["mission_id"])
        inventory = AgentInbox(path).inventory()
        ok = all((
            first["mission_id"] == replay["mission_id"], duplicate_prevented, evidence_blocked,
            done["status"] == "done", persisted["status"] == "done",
            recovered == {"recovered": 1, "failed": 0}, second_claim["attempts"] == 2,
            exhausted == {"recovered": 0, "failed": 1},
            AgentInbox(path).get(second["mission_id"])["status"] == "failed",
            inventory["signals"] == {"disagreements": 1, "escalations": 1},
        ))
        return {
            "probe": "functional", "ok": ok, "idempotent_enqueue": first["mission_id"] == replay["mission_id"],
            "duplicate_claim_prevented": duplicate_prevented, "proofless_done_blocked": evidence_blocked,
            "done_persisted": persisted["status"] == "done", "lease_recovered_once": recovered["recovered"] == 1,
            "retry_exhaustion_failed": exhausted["failed"] == 1,
            "disagreements": inventory["signals"]["disagreements"], "escalations": inventory["signals"]["escalations"],
            "evidence_sha256": done["evidence_sha256"],
        }

