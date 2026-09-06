from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_budgeter.engine import BudgetEngine
from agent_budgeter.journal import EvidenceJournal
from agent_budgeter.models import (
    AgentProfile, BudgetVector, ContractError, Decision, Mission, MissionState,
)
from agent_budgeter.registry import AgentRegistry


def make_engine(global_limit=BudgetVector(10,1000,10000), agent_limit=BudgetVector(8,800,8000), mission_limit=BudgetVector(5,500,5000), max_retries=1):
    profile=AgentProfile("agent","owner",("code",),("local",),agent_limit,max_retries)
    engine=BudgetEngine(global_limit,AgentRegistry((profile,)))
    mission=Mission("mission","agent","code","local",mission_limit); engine.add_mission(mission)
    return engine,mission


class VectorTests(unittest.TestCase):
    def test_zero(self): self.assertEqual(BudgetVector.zero(),BudgetVector(0,0,0))
    def test_add(self): self.assertEqual(BudgetVector(1,2,3).add(BudgetVector(2,3,4)),BudgetVector(3,5,7))
    def test_subtract(self): self.assertEqual(BudgetVector(3,5,7).subtract(BudgetVector(2,3,4)),BudgetVector(1,2,3))
    def test_negative_rejected(self):
        with self.assertRaises(ContractError): BudgetVector(-1,0,0)
    def test_underflow_rejected(self):
        with self.assertRaises(ContractError): BudgetVector(1,1,1).subtract(BudgetVector(2,0,0))
    def test_fits_each_dimension(self):
        self.assertTrue(BudgetVector(1,1,1).fits(BudgetVector(1,1,1)))
        self.assertFalse(BudgetVector(2,1,1).fits(BudgetVector(1,9,9)))
    def test_roundtrip(self): self.assertEqual(BudgetVector.from_dict(BudgetVector(1,2,3).to_dict()),BudgetVector(1,2,3))
    def test_unknown_field_rejected(self):
        with self.assertRaises(ContractError): BudgetVector.from_dict({"calls":1,"time_ms":2,"tokens":3,"x":4})


class RegistryStateTests(unittest.TestCase):
    def test_registry_inventory(self):
        profile=AgentProfile("a","o",("c",),("p",),BudgetVector(1,1,1),0)
        self.assertEqual(AgentRegistry((profile,)).inventory()[0]["owner"],"o")
    def test_duplicate_agent_rejected(self):
        profile=AgentProfile("a","o",("c",),("p",),BudgetVector(1,1,1),0)
        with self.assertRaises(ContractError): AgentRegistry((profile,profile))
    def test_duplicate_capability_rejected(self):
        with self.assertRaises(ContractError): AgentProfile("a","o",("c","c"),("p",),BudgetVector(1,1,1),0)
    def test_retry_bound(self):
        with self.assertRaises(ContractError): AgentProfile("a","o",("c",),("p",),BudgetVector(1,1,1),101)
    def test_valid_transitions(self):
        _,mission=make_engine(); mission.transition(MissionState.RUNNING); mission.transition(MissionState.WAITING); mission.transition(MissionState.RUNNING); mission.transition(MissionState.DONE)
        self.assertEqual(mission.state,MissionState.DONE)
    def test_invalid_transition(self):
        _,mission=make_engine()
        with self.assertRaises(ContractError): mission.transition(MissionState.DONE)
    def test_terminal_cannot_transition(self):
        _,mission=make_engine(); mission.transition(MissionState.REJECTED)
        with self.assertRaises(ContractError): mission.transition(MissionState.RUNNING)


class EngineTests(unittest.TestCase):
    def test_reserve_accepts_and_starts(self):
        engine,mission=make_engine(); result=engine.reserve("op",mission.mission_id,BudgetVector(1,10,100))
        self.assertEqual(result.decision,Decision.ACCEPTED); self.assertEqual(mission.state,MissionState.RUNNING)
    def test_reserve_idempotent(self):
        engine,mission=make_engine(); first=engine.reserve("op",mission.mission_id,BudgetVector(1,10,100)); second=engine.reserve("op",mission.mission_id,BudgetVector(1,10,100))
        self.assertEqual(first.evidence_sha256,second.evidence_sha256); self.assertEqual(len(engine.reservations),1)
    def test_idempotency_conflict_blocks(self):
        engine,mission=make_engine(); engine.reserve("op",mission.mission_id,BudgetVector(1,10,100)); result=engine.reserve("op",mission.mission_id,BudgetVector(2,10,100))
        self.assertEqual(result.decision,Decision.BLOCKED)
    def test_global_limit(self):
        engine,mission=make_engine(global_limit=BudgetVector(0,1000,10000)); result=engine.reserve("op",mission.mission_id,BudgetVector(1,0,0))
        self.assertEqual(result.decision,Decision.REJECTED); self.assertIn("global",result.reason)
    def test_mission_limit(self):
        engine,mission=make_engine(mission_limit=BudgetVector(0,500,5000)); result=engine.reserve("op",mission.mission_id,BudgetVector(1,0,0))
        self.assertIn("mission",result.reason)
    def test_agent_limit(self):
        engine,mission=make_engine(agent_limit=BudgetVector(0,800,8000)); result=engine.reserve("op",mission.mission_id,BudgetVector(1,0,0))
        self.assertIn("agent",result.reason)
    def test_capability_denied(self):
        engine,mission=make_engine(); mission.required_capability="missing"; result=engine.reserve("op",mission.mission_id,BudgetVector.zero())
        self.assertEqual(result.decision,Decision.REJECTED); self.assertEqual(mission.state,MissionState.REJECTED)
    def test_permission_denied(self):
        engine,mission=make_engine(); mission.required_permission="remote"; self.assertEqual(engine.reserve("op",mission.mission_id,BudgetVector.zero()).decision,Decision.REJECTED)
    def test_terminal_reserve_rejected(self):
        engine,mission=make_engine(); mission.state=MissionState.DONE
        self.assertEqual(engine.reserve("op",mission.mission_id,BudgetVector.zero()).decision,Decision.REJECTED)
    def test_consume_accepted(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(2,100,100)).reservation
        result=engine.consume("c",reservation.reservation_id,calls=1,time_ms=50,tokens=60)
        self.assertEqual(result.decision,Decision.ACCEPTED); self.assertEqual(result.reservation.consumed,BudgetVector(1,50,60))
    def test_cumulative_consume(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(2,100,100)).reservation
        engine.consume("c1",reservation.reservation_id,calls=1,time_ms=20,tokens=30); result=engine.consume("c2",reservation.reservation_id,calls=1,time_ms=30,tokens=40)
        self.assertEqual(result.reservation.consumed,BudgetVector(2,50,70))
    def test_overconsume_blocked_failed(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(1,10,10)).reservation
        result=engine.consume("c",reservation.reservation_id,calls=2,time_ms=0,tokens=0)
        self.assertEqual(result.decision,Decision.BLOCKED); self.assertEqual(mission.state,MissionState.FAILED)
    def test_unknown_measure_blocked(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(1,10,10)).reservation
        result=engine.consume("c",reservation.reservation_id,calls=1,time_ms=None,tokens=1)
        self.assertEqual(result.decision,Decision.BLOCKED); self.assertEqual(mission.state,MissionState.FAILED)
    def test_unknown_reservation_blocked(self):
        engine,_=make_engine(); self.assertEqual(engine.consume("c","missing",calls=0,time_ms=0,tokens=0).decision,Decision.BLOCKED)
    def test_release_and_replay(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(1,10,10)).reservation
        first=engine.release("x",reservation.reservation_id); second=engine.release("x",reservation.reservation_id)
        self.assertTrue(first.reservation.released); self.assertEqual(first.evidence_sha256,second.evidence_sha256)
    def test_release_preserves_consumed_usage(self):
        engine,mission=make_engine(global_limit=BudgetVector(1,10,10)); reservation=engine.reserve("r",mission.mission_id,BudgetVector(1,10,10)).reservation
        engine.consume("c",reservation.reservation_id,calls=1,time_ms=10,tokens=10); engine.release("x",reservation.reservation_id)
        mission2=Mission("m2","agent","code","local",BudgetVector(1,10,10)); engine.add_mission(mission2)
        self.assertEqual(engine.reserve("r2","m2",BudgetVector(1,0,0)).decision,Decision.REJECTED)
    def test_retry_accepted(self):
        engine,mission=make_engine(); mission.state=MissionState.WAITING
        self.assertEqual(engine.retry("op",mission.mission_id).decision,Decision.ACCEPTED); self.assertEqual(mission.retries,1)
    def test_retry_limit(self):
        engine,mission=make_engine(max_retries=0); mission.state=MissionState.WAITING
        self.assertEqual(engine.retry("op",mission.mission_id).decision,Decision.REJECTED); self.assertEqual(mission.state,MissionState.FAILED)
    def test_retry_wrong_state(self):
        engine,mission=make_engine(); self.assertEqual(engine.retry("op",mission.mission_id).decision,Decision.REJECTED)
    def test_metrics(self):
        engine,mission=make_engine(); reservation=engine.reserve("r",mission.mission_id,BudgetVector(1,10,10)).reservation; engine.consume("c",reservation.reservation_id,calls=1,time_ms=4,tokens=5)
        metrics=engine.metrics(); self.assertEqual(metrics["consumption"],{"calls":1,"time_ms":4,"tokens":5}); self.assertEqual(metrics["missions"]["running"],1)
    def test_duplicate_mission_rejected(self):
        engine,mission=make_engine()
        with self.assertRaises(ContractError): engine.add_mission(mission)


if __name__ == "__main__": unittest.main()

