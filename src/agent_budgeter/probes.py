"""Operational probes and deterministic failure counter-proof."""

from __future__ import annotations

from .engine import BudgetEngine
from .models import AgentProfile, BudgetVector, Decision, Mission
from .registry import AgentRegistry


def liveness() -> dict: return {"schema_version":"1.0","mode":"liveness","healthy":True,"checks":[{"name":"process","passed":True}]}
def readiness() -> dict:
    try: BudgetVector(1, 1, 1); passed=True
    except Exception: passed=False
    return {"schema_version":"1.0","mode":"readiness","healthy":passed,"checks":[{"name":"contracts","passed":passed}]}


def _engine(limit: BudgetVector = BudgetVector(10, 1000, 10000)) -> tuple[BudgetEngine, Mission]:
    profile = AgentProfile("agent-1","synthetic-owner",("code",),("local",),limit,1)
    engine = BudgetEngine(limit, AgentRegistry((profile,)))
    mission = Mission("mission-1","agent-1","code","local",limit); engine.add_mission(mission)
    return engine, mission


def functional() -> dict:
    engine, mission = _engine()
    control = engine.reserve("control-reserve", mission.mission_id, BudgetVector(1,100,100))
    replay = engine.reserve("control-reserve", mission.mission_id, BudgetVector(1,100,100))
    consumed = engine.consume("control-consume", control.reservation.reservation_id, calls=1,time_ms=90,tokens=80)  # type: ignore[union-attr]
    unknown = engine.consume("unknown-measure", control.reservation.reservation_id, calls=0,time_ms=None,tokens=0)  # type: ignore[union-attr]
    over_engine, over_mission = _engine(BudgetVector(1,100,100))
    over = over_engine.reserve("over", over_mission.mission_id, BudgetVector(2,100,100))
    checks = [
        {"name":"control_accepted","passed":control.decision is Decision.ACCEPTED and consumed.decision is Decision.ACCEPTED},
        {"name":"idempotent_replay","passed":control.evidence_sha256 == replay.evidence_sha256},
        {"name":"unknown_measurement_blocked","passed":unknown.decision is Decision.BLOCKED},
        {"name":"over_budget_rejected","passed":over.decision is Decision.REJECTED},
    ]
    return {"schema_version":"1.0","mode":"functional","healthy":all(c["passed"] for c in checks),"checks":checks}

