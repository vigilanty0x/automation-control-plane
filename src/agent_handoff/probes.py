"""Independent health and functional counter-proof probes."""

from __future__ import annotations

from copy import deepcopy
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .ledger import HandoffLedger
from .models import ContractError, Handoff


def liveness_probe() -> dict[str, Any]:
    return {"ok": True, "probe": "liveness", "version": __version__}


def readiness_probe() -> dict[str, Any]:
    return {"ok": True, "probe": "readiness", "schema_versions": ["1.0"], "runtime_dependencies": [], "formats": ["json", "markdown"]}


def fixture() -> dict[str, Any]:
    return {
        "schema_version": "1.0", "handoff_id": "probe-handoff", "mission_id": "probe-mission",
        "sequence": 1, "state": "done", "from_agent": "builder", "to_agent": "reviewer",
        "owner": "reviewer", "created_at": "2026-08-15T00:00:00Z", "path_scope": ["src/probe.py"],
        "capabilities": ["python"], "permissions": ["read"], "limits": {"max_retries": 2},
        "criteria": [{"criterion_id": "tests", "description": "Tests pass", "met": True}],
        "evidence": [{"evidence_id": "test-run", "kind": "test", "uri": "artifact://tests", "sha256": "1" * 64, "summary": "Synthetic tests passed"}],
        "open_items": [], "summary": "Synthetic functional handoff.",
    }


def functional_probe() -> dict[str, Any]:
    control = Handoff.from_dict(fixture())
    counter = deepcopy(fixture())
    counter["evidence"] = []
    counter_failed = False
    try:
        Handoff.from_dict(counter)
    except ContractError:
        counter_failed = True
    with tempfile.TemporaryDirectory() as directory:
        ledger = HandoffLedger(Path(directory) / "handoffs.jsonl")
        _, first = ledger.append(control)
        _, replay = ledger.append(control)
        verified = ledger.verify()["valid"]
    return {"ok": counter_failed and first and not replay and verified, "probe": "functional", "control_passed": True, "counter_example_failed": counter_failed, "idempotent_replay": not replay, "ledger_verified": verified}

