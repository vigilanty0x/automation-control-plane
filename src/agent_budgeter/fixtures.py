"""Strict synthetic workload replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .engine import BudgetEngine
from .journal import EvidenceJournal
from .models import AgentProfile, BudgetVector, ContractError, Mission, MissionState
from .registry import AgentRegistry


def _profile(value: Mapping[str, Any]) -> AgentProfile:
    if set(value) != {"agent_id","owner","capabilities","permissions","limit","max_retries"}: raise ContractError("agent fields invalid")
    return AgentProfile(value["agent_id"], value["owner"], tuple(value["capabilities"]), tuple(value["permissions"]),
                        BudgetVector.from_dict(value["limit"]), value["max_retries"])


def _mission(value: Mapping[str, Any]) -> Mission:
    if set(value) != {"mission_id","agent_id","required_capability","required_permission","limit"}: raise ContractError("mission fields invalid")
    return Mission(value["mission_id"], value["agent_id"], value["required_capability"], value["required_permission"], BudgetVector.from_dict(value["limit"]))


def replay(value: Mapping[str, Any], journal: EvidenceJournal | None = None) -> dict[str, Any]:
    if set(value) != {"schema_version","global_limit","agents","missions","operations"} or value["schema_version"] != "1.0": raise ContractError("fixture fields invalid")
    if not all(isinstance(value[key], list) and len(value[key]) <= 1000 for key in ("agents","missions","operations")): raise ContractError("fixture collections invalid")
    registry = AgentRegistry(tuple(_profile(item) for item in value["agents"]))
    engine = BudgetEngine(BudgetVector.from_dict(value["global_limit"]), registry, journal)
    for item in value["missions"]: engine.add_mission(_mission(item))
    results = []
    for operation in value["operations"]:
        action, operation_id = operation.get("action"), operation.get("operation_id")
        if action == "reserve": result = engine.reserve(operation_id, operation["mission_id"], BudgetVector.from_dict(operation["amount"]))
        elif action == "consume": result = engine.consume(operation_id, operation["reservation_id"], calls=operation.get("calls"), time_ms=operation.get("time_ms"), tokens=operation.get("tokens"))
        elif action == "release": result = engine.release(operation_id, operation["reservation_id"])
        elif action == "retry": result = engine.retry(operation_id, operation["mission_id"])
        elif action == "transition":
            engine.transition(operation["mission_id"], MissionState(operation["state"])); continue
        else: raise ContractError("unsupported fixture operation")
        results.append(result.to_dict())
        if result.reservation: operation["resolved_reservation_id"] = result.reservation.reservation_id
    return {"schema_version":"1.0","registry":registry.inventory(),"results":results,"metrics":engine.metrics(),
            "missions":[engine.missions[key].to_dict() for key in sorted(engine.missions)]}


def load(path: Path) -> Mapping[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ContractError(f"fixture unreadable: {exc}") from exc
    if not isinstance(value, dict): raise ContractError("fixture root must be object")
    return value

