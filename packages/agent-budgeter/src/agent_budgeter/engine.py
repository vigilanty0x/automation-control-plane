"""Transactional in-memory budget ledger with append-only evidence."""

from __future__ import annotations

from dataclasses import replace
import threading
from typing import Any

from .journal import EvidenceJournal
from .models import (
    AgentProfile, BudgetVector, ContractError, Decision, Mission, MissionState, OperationResult, Reservation,
    sha256_json,
)
from .registry import AgentRegistry


class BudgetEngine:
    def __init__(self, global_limit: BudgetVector, registry: AgentRegistry, journal: EvidenceJournal | None = None) -> None:
        self.global_limit, self.registry, self.journal = global_limit, registry, journal
        self.missions: dict[str, Mission] = {}; self.reservations: dict[str, Reservation] = {}
        self.operations: dict[str, tuple[str, OperationResult]] = {}; self._lock = threading.RLock()
        self.rejections = 0; self.interventions = 0

    def add_mission(self, mission: Mission) -> None:
        with self._lock:
            if mission.mission_id in self.missions: raise ContractError("mission id already exists")
            self.missions[mission.mission_id] = mission

    def transition(self, mission_id: str, target: MissionState) -> None:
        with self._lock: self._mission(mission_id).transition(target)

    def _mission(self, mission_id: str) -> Mission:
        mission = self.missions.get(mission_id)
        if mission is None: raise ContractError("unknown mission")
        return mission

    def _usage(self, *, mission_id: str | None = None, agent_id: str | None = None) -> BudgetVector:
        total = BudgetVector.zero()
        for reservation in self.reservations.values():
            if mission_id is not None and reservation.mission_id != mission_id: continue
            if agent_id is not None and reservation.agent_id != agent_id: continue
            total = total.add(reservation.consumed if reservation.released else reservation.amount)
        return total

    def _idempotent(self, operation_id: str, fingerprint: str) -> OperationResult | None:
        prior = self.operations.get(operation_id)
        if prior is None: return None
        if prior[0] != fingerprint:
            self.interventions += 1
            return OperationResult.create(operation_id, Decision.BLOCKED, "idempotency", "operation id reused with different input")
        return prior[1]

    def _record(self, fingerprint: str, result: OperationResult) -> OperationResult:
        self.operations[result.operation_id] = (fingerprint, result)
        if result.decision is not Decision.ACCEPTED: self.rejections += 1
        if self.journal: self.journal.append(result)
        return result

    def reserve(self, operation_id: str, mission_id: str, requested: BudgetVector) -> OperationResult:
        fingerprint = sha256_json({"action":"reserve","mission":mission_id,"requested":requested.to_dict()})
        with self._lock:
            if prior := self._idempotent(operation_id, fingerprint): return prior
            mission = self._mission(mission_id); profile = self.registry.get(mission.agent_id)
            if profile is None: return self._record(fingerprint, OperationResult.create(operation_id, Decision.BLOCKED, "reserve", "agent missing from registry"))
            if mission.state not in {MissionState.QUEUED, MissionState.RUNNING, MissionState.WAITING}:
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.REJECTED, "reserve", "mission is terminal"))
            if mission.required_capability not in profile.capabilities or mission.required_permission not in profile.permissions:
                mission.state = MissionState.REJECTED
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.REJECTED, "reserve", "capability or permission denied"))
            checks = ((self._usage().add(requested), self.global_limit, "global"),
                      (self._usage(mission_id=mission_id).add(requested), mission.limit, "mission"),
                      (self._usage(agent_id=mission.agent_id).add(requested), profile.limit, "agent"))
            for projected, limit, scope in checks:
                if not projected.fits(limit):
                    mission.state = MissionState.REJECTED
                    return self._record(fingerprint, OperationResult.create(operation_id, Decision.REJECTED, "reserve", f"{scope} budget exceeded"))
            reservation_id = sha256_json({"operation_id":operation_id,"mission":mission_id,"amount":requested.to_dict()})
            reservation = Reservation(reservation_id, mission_id, mission.agent_id, requested, BudgetVector.zero(), False)
            self.reservations[reservation_id] = reservation
            if mission.state is MissionState.QUEUED: mission.state = MissionState.RUNNING
            return self._record(fingerprint, OperationResult.create(operation_id, Decision.ACCEPTED, "reserve", "budget reserved", reservation))

    def consume(self, operation_id: str, reservation_id: str, *, calls: int | None, time_ms: int | None, tokens: int | None) -> OperationResult:
        fingerprint = sha256_json({"action":"consume","reservation":reservation_id,"calls":calls,"time_ms":time_ms,"tokens":tokens})
        with self._lock:
            if prior := self._idempotent(operation_id, fingerprint): return prior
            reservation = self.reservations.get(reservation_id)
            if reservation is None: return self._record(fingerprint, OperationResult.create(operation_id, Decision.BLOCKED, "consume", "unknown reservation"))
            mission = self._mission(reservation.mission_id)
            if None in {calls, time_ms, tokens}:
                mission.state = MissionState.FAILED
                self.interventions += 1
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.BLOCKED, "consume", "measurement unknown; mission failed closed", reservation))
            measured = BudgetVector(calls, time_ms, tokens)  # type: ignore[arg-type]
            cumulative = reservation.consumed.add(measured)
            if reservation.released or not cumulative.fits(reservation.amount):
                mission.state = MissionState.FAILED; self.interventions += 1
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.BLOCKED, "consume", "consumption exceeds or follows released reservation", reservation))
            updated = replace(reservation, consumed=cumulative); self.reservations[reservation_id] = updated
            return self._record(fingerprint, OperationResult.create(operation_id, Decision.ACCEPTED, "consume", "consumption recorded", updated))

    def release(self, operation_id: str, reservation_id: str) -> OperationResult:
        fingerprint = sha256_json({"action":"release","reservation":reservation_id})
        with self._lock:
            if prior := self._idempotent(operation_id, fingerprint): return prior
            reservation = self.reservations.get(reservation_id)
            if reservation is None: return self._record(fingerprint, OperationResult.create(operation_id, Decision.BLOCKED, "release", "unknown reservation"))
            if reservation.released: return self._record(fingerprint, OperationResult.create(operation_id, Decision.ACCEPTED, "release", "reservation already released", reservation))
            updated = replace(reservation, released=True); self.reservations[reservation_id] = updated
            return self._record(fingerprint, OperationResult.create(operation_id, Decision.ACCEPTED, "release", "unused budget released", updated))

    def retry(self, operation_id: str, mission_id: str) -> OperationResult:
        fingerprint = sha256_json({"action":"retry","mission":mission_id})
        with self._lock:
            if prior := self._idempotent(operation_id, fingerprint): return prior
            mission = self._mission(mission_id); profile = self.registry.get(mission.agent_id)
            if profile is None or mission.retries >= profile.max_retries:
                mission.state = MissionState.FAILED; self.interventions += 1
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.REJECTED, "retry", "retry limit exceeded"))
            if mission.state is not MissionState.WAITING:
                return self._record(fingerprint, OperationResult.create(operation_id, Decision.REJECTED, "retry", "mission is not waiting"))
            mission.retries += 1; mission.state = MissionState.RUNNING
            return self._record(fingerprint, OperationResult.create(operation_id, Decision.ACCEPTED, "retry", "retry accepted"))

    def metrics(self) -> dict[str, Any]:
        consumed = BudgetVector.zero()
        for reservation in self.reservations.values(): consumed = consumed.add(reservation.consumed)
        return {"schema_version":"1.0","consumption":consumed.to_dict(),"rejections":self.rejections,
                "interventions":self.interventions,"missions":{state.value:sum(m.state is state for m in self.missions.values()) for state in MissionState}}
