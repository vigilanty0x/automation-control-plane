"""Durable native execution quotas, never provider token/currency accounting.

Reservations and observed dispatches share the Factory Store and journal.
An owner is a declared quota group, not an authenticated identity.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import math

RESOURCES = ("executor_calls", "retained_output_bytes", "execution_ms")
MAX_VALUE = 1_000_000_000_000
MAX_CALLS = 10_000
QUOTA_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_reservations (
 run_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
 record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,task_id,attempt),
 FOREIGN KEY(run_id,task_id) REFERENCES tasks(run_id,task_id)
);
CREATE TABLE IF NOT EXISTS execution_dispatches (
 run_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
 ordinal INTEGER NOT NULL, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,task_id,attempt,ordinal),
 FOREIGN KEY(run_id,task_id,attempt) REFERENCES execution_reservations(run_id,task_id,attempt)
);
"""


class QuotaError(ValueError):
    """Quota evidence cannot authorize another execution/publication."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _integer(value, maximum=MAX_VALUE):
    if type(value) is not int or not 0 <= value <= maximum:
        raise QuotaError("quota counts must be bounded non-boolean nonnegative integers")
    return value


def _vector(value):
    if not isinstance(value, dict) or set(value) != set(RESOURCES):
        raise QuotaError("quota requires executor_calls, retained_output_bytes, execution_ms; tokens/cost are not measured")
    return {key: _integer(value[key], MAX_CALLS if key == "executor_calls" else MAX_VALUE) for key in RESOURCES}


def parse_quota(value):
    if not isinstance(value, dict) or not {"limits"} <= set(value) <= {"limits", "owners"}:
        raise QuotaError("execution_quota requires limits and optional owners")
    owners = value.get("owners", {})
    if not isinstance(owners, dict) or len(owners) > 1000:
        raise QuotaError("quota owners must be a bounded object of declared groups")
    if any(not isinstance(key, str) or not key.strip() or len(key) > 128 for key in owners):
        raise QuotaError("quota owner must be a bounded nonempty declared group")
    return {"limits": _vector(value["limits"]), "owners": {key: _vector(owners[key]) for key in sorted(owners)}}


def _zero():
    return dict.fromkeys(RESOURCES, 0)


def _sum(values):
    output = _zero()
    for value in values:
        for key in RESOURCES:
            output[key] += value[key]
            if output[key] > MAX_VALUE:
                raise QuotaError("quota aggregate overflow")
    return output


def _fits(value, limit):
    return all(value[key] <= limit[key] for key in RESOURCES)


def reservation_contract(spec, run_id, task_id, attempt):
    """Deterministic upper admission bounds from the exact static task/test spec."""
    from .executors import command_digest
    task = spec.task(task_id)
    steps = [(task.id, task.command, task.timeout_seconds or spec.budget.default_task_timeout_seconds)]
    steps += [(f"{task.id}:test:{test.name}", test.command,
               test.timeout_seconds or task.timeout_seconds or spec.budget.default_task_timeout_seconds) for test in task.tests]
    declarations = [{"ordinal": index, "label": label, "command_digest": command_digest(argv),
                     "environment_sha256": _digest(dict(task.environment)),
                     "timeout_ms": math.ceil(timeout * 1000), "output_cap_bytes": spec.budget.max_output_bytes}
                    for index, (label, argv, timeout) in enumerate(steps)]
    reserved = _sum({"executor_calls": 1, "retained_output_bytes": step["output_cap_bytes"],
                     "execution_ms": step["timeout_ms"]} for step in declarations)
    _vector(reserved)
    return {"format": "factory-execution-reservation-v1", "run_id": run_id, "task_id": task_id,
            "attempt": attempt, "spec_sha256": sha256(spec.canonical_json().encode()).hexdigest(),
            "owner_group": task.owner, "owner_authentication": "not_established", "steps": declarations,
            "reserved": reserved}


def request_material(request):
    from .executors import command_digest
    if (type(request.timeout_seconds) not in (int, float) or not math.isfinite(request.timeout_seconds)
            or request.timeout_seconds <= 0 or type(request.max_output_bytes) is not int or request.max_output_bytes <= 0):
        raise QuotaError("dispatch bounds invalid")
    return {"label": request.label, "command_digest": command_digest(request.argv),
            "environment_sha256": _digest(dict(request.environment)),
            "timeout_ms": math.ceil(request.timeout_seconds * 1000), "output_cap_bytes": request.max_output_bytes}


def _measured(dispatches):
    return _sum(item["measurement"] for item in dispatches if item["measurement"] is not None)


def _occupied(reservation, dispatches):
    measured = _measured(dispatches)
    if reservation["state"] == "settled":
        return measured
    return {key: max(reservation["contract"]["reserved"][key], measured[key]) for key in RESOURCES}


def _receipt(reservation, dispatches):
    unknown = any(item["state"] in {"started", "unknown"} for item in dispatches)
    return {"format": "factory-execution-usage-v1", "contract_sha256": _digest(reservation["contract"]),
            "reserved": reservation["contract"]["reserved"], "dispatches": dispatches,
            "known_consumption": _measured(dispatches), "measurement_complete": not unknown,
            "consumption": None if unknown else _measured(dispatches),
            "unknown_dispatches": sum(item["state"] in {"started", "unknown"} for item in dispatches),
            "tokens": None, "cost": None, "provider_usage": "not_measured",
            "owner_authentication": "not_established", "producer_authentication": "not_established"}


def _timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise QuotaError("quota timestamp invalid")
    return value


def replay_quota(spec, run_id, events):
    """Replay native quota facts; hashes prove coherence, not producer identity."""
    reservations, dispatches, claims, completions = {}, {}, set(), {}
    previous_time = 0
    for event in events:
        now = _timestamp(event['created_at'])
        if now < previous_time:
            raise QuotaError("quota journal clock regressed")
        previous_time = now
        kind, payload = event['event_type'], event['payload']
        if kind not in {'quota.reservation', 'quota.dispatch'}:
            if event['event_key'].startswith('task.claimed:'):
                key = (event['task_id'], _integer(payload['attempt'], 100))
                if event['event_key'] != f'task.claimed:{key[0]}:{key[1]}':
                    raise QuotaError("claim identity invalid")
                if key not in reservations or key in claims or reservations[key]['state'] != 'held':
                    raise QuotaError("claim has no unique held reservation")
                claims.add(key)
            elif event['event_key'].startswith('task.completed:'):
                key = (event['task_id'], _integer(payload['attempt'], 100))
                if event['event_key'] != f'task.completed:{key[0]}:{key[1]}':
                    raise QuotaError("completion identity invalid")
                if key not in claims or key in completions:
                    raise QuotaError("quota completion has no unique claim")
                value, calls = reservations[key], dispatches.get(key, [])
                if value['state'] == 'held':
                    raise QuotaError("completion left quota unsettled")
                if payload['to'] == 'succeeded' and (value['state'] != 'settled' or len(calls) != len(value['contract']['steps']) or any(item['state'] != 'measured' for item in calls)):
                    raise QuotaError("successful completion has incomplete quota evidence")
                if payload['to'] == 'succeeded' and any(item['state'] in {'unknown', 'overrun'} for item in reservations.values()):
                    raise QuotaError("publication after uncertain/overrun consumption")
                completions[key] = _receipt(value, calls)
            continue
        if not isinstance(payload, dict) or set(payload) != {'attempt', 'ordinal', 'record', 'record_sha256'}:
            raise QuotaError("quota event shape invalid")
        attempt = _integer(payload['attempt'], 100)
        if attempt < 1:
            raise QuotaError("quota attempt invalid")
        key = (event['task_id'], attempt)
        value = payload['record']
        if not isinstance(value, dict) or _digest(value) != payload['record_sha256']:
            raise QuotaError("quota event digest invalid")
        if kind == 'quota.reservation':
            if set(value) != {'contract', 'state', 'reason'} or payload['ordinal'] is not None or _json(value['contract']) != _json(reservation_contract(spec, run_id, *key)):
                raise QuotaError("quota reservation contract invalid")
            old, calls = reservations.get(key), dispatches.get(key, [])
            state = value['state']
            if old is None:
                if state != 'held' or value['reason'] is not None:
                    raise QuotaError("reservation must begin held")
                if any(item['state'] in {'unknown', 'overrun'} for item in reservations.values()):
                    raise QuotaError("new reserve after uncertainty/overrun")
                policy, owner = spec.budget.execution_quota, value['contract']['owner_group']
                for group, limit in ((None, policy['limits']), (owner, policy['owners'].get(owner))):
                    occupied = [_occupied(item, dispatches.get(identity, [])) for identity, item in reservations.items()
                                if group is None or item['contract']['owner_group'] == group]
                    if limit is not None and not _fits(_sum([*occupied, value['contract']['reserved']]), limit):
                        raise QuotaError("journal reservation exceeded declared quota")
            else:
                if old['state'] != 'held' or key in completions:
                    raise QuotaError("quota terminal reservation changed")
                if state == 'settled':
                    if value['reason'] not in {'completion', 'never_dispatched'} or any(item['state'] != 'measured' for item in calls) or (value['reason'] == 'never_dispatched' and calls):
                        raise QuotaError("quota released unmeasured execution")
                elif state == 'unknown':
                    if not calls or value['reason'] != 'interrupted_after_dispatch':
                        raise QuotaError("quota uncertainty reason invalid")
                elif state == 'overrun':
                    exceeded = any(item['measurement'] is not None and
                        (item['measurement']['execution_ms'] > item['request']['timeout_ms'] or item['measurement']['retained_output_bytes'] > item['request']['output_cap_bytes']) for item in calls)
                    if not exceeded or value['reason'] != 'observed_execution_exceeded_reservation':
                        raise QuotaError("quota overrun lacks measurement")
                else:
                    raise QuotaError("quota reservation transition invalid")
            reservations[key] = value
        else:
            if set(value) != {'ordinal', 'request', 'state', 'measurement', 'measurement_origin', 'started_at', 'finished_at'} or key not in claims or key in completions:
                raise QuotaError("dispatch shape or claim invalid")
            ordinal = _integer(value['ordinal'], MAX_CALLS)
            calls = dispatches.setdefault(key, [])
            if type(payload['ordinal']) is not int or payload['ordinal'] != ordinal or ordinal >= len(reservations[key]['contract']['steps']):
                raise QuotaError("dispatch ordinal invalid")
            actual, expected = value['request'], reservations[key]['contract']['steps'][ordinal]
            if not isinstance(actual, dict) or set(actual) != {'label', 'command_digest', 'environment_sha256', 'timeout_ms', 'output_cap_bytes'}:
                raise QuotaError("dispatch request shape invalid")
            if any(actual[k] != expected[k] for k in ('label', 'command_digest', 'environment_sha256')) or not 0 < _integer(actual['timeout_ms']) <= expected['timeout_ms'] or not 0 < _integer(actual['output_cap_bytes']) <= expected['output_cap_bytes']:
                raise QuotaError("dispatch differs from reserved command")
            _timestamp(value['started_at'])
            if ordinal == len(calls):
                if any(item['state'] in {'unknown', 'overrun'} for item in reservations.values()):
                    raise QuotaError("dispatch after uncertain/overrun consumption")
                if reservations[key]['state'] != 'held' or value['state'] != 'started' or value['measurement'] is not None or value['measurement_origin'] is not None or value['finished_at'] is not None or value['started_at'] != now or any(item['state'] != 'measured' for item in calls):
                    raise QuotaError("dispatch not admitted in order")
                calls.append(value)
            elif ordinal < len(calls):
                old = calls[ordinal]
                if old['state'] != 'started' or any(old[k] != value[k] for k in ('ordinal', 'request', 'started_at')):
                    raise QuotaError("dispatch replay/identity changed")
                if value['state'] == 'measured':
                    measurement = _vector(value['measurement'])
                    if measurement['executor_calls'] != 1 or value['measurement_origin'] not in {'caller_declared', 'engine_monotonic_output'} or _timestamp(value['finished_at']) != now or now < value['started_at']:
                        raise QuotaError("dispatch measurement/time invalid")
                elif value['state'] == 'unknown':
                    if value['measurement'] is not None or value['measurement_origin'] is not None or value['finished_at'] is not None:
                        raise QuotaError("unknown dispatch must not invent measurement")
                else:
                    raise QuotaError("dispatch transition invalid")
                calls[ordinal] = value
            else:
                raise QuotaError("dispatch ordinal gap")
        ordinal = payload['ordinal']
        if event['event_key'] != f"{kind}:{key[0]}:{key[1]}:{ordinal}:{value['state']}":
            raise QuotaError("quota event identity invalid")
    return reservations, dispatches, completions


def verify_quota_evidence(spec, run_id, events, receipts, status):
    if spec.budget.execution_quota is None:
        return []
    try:
        reservations, dispatches, completions = replay_quota(spec, run_id, events)
        for item in receipts:
            key = (item['task_id'], item['attempt'])
            if _json(item['receipt'].get('execution_quota')) != _json(completions.get(key)):
                raise QuotaError("receipt differs from replayed quota facts")
            if item['receipt']['outcome'] == 'succeeded':
                summaries = [item['receipt']['execution'], *item['receipt']['tests']]
                calls = dispatches[key]
                if len(summaries) != len(calls):
                    raise QuotaError("successful receipt omitted dispatch")
                for summary, call in zip(summaries, calls):
                    if summary['label'] != call['request']['label'] or summary['command_digest'] != call['request']['command_digest'] or summary['stdout']['captured_bytes'] + summary['stderr']['captured_bytes'] != call['measurement']['retained_output_bytes']:
                        raise QuotaError("successful receipt output differs from quota measurement")
        summary = status['execution_quota']
        known = _sum(_measured(calls) for calls in dispatches.values())
        complete = not any(call['state'] in {'started', 'unknown'} for calls in dispatches.values() for call in calls)
        expected = {'limits': spec.budget.execution_quota,
            'occupied': _sum(_occupied(value, dispatches.get(key, [])) for key, value in reservations.items()),
            'known_consumption': known, 'consumption': known if complete else None, 'measurement_complete': complete,
            'tokens': None, 'cost': None, 'owner_authentication': 'not_established',
            'reservation_states': [{'task_id': key[0], 'attempt': key[1], 'state': reservations[key]['state'], 'reason': reservations[key]['reason']} for key in sorted(reservations)],
            'uncertain_or_overrun': any(value['state'] in {'unknown', 'overrun'} for value in reservations.values())}
        if any(_json(summary.get(key)) != _json(value) for key, value in expected.items()):
            raise QuotaError("quota status differs from journal consumption")
        return []
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        return [f"quota evidence invalid: {exc}"]


class QuotaStoreMixin:
    def _quota_spec(self, connection, run_id):
        _, spec = self._approval_spec(connection, run_id)
        return spec

    def _quota_write(self, connection, run_id, task_id, attempt, record, now, ordinal=None):
        table = "execution_reservations" if ordinal is None else "execution_dispatches"
        columns = "run_id,task_id,attempt" + (",ordinal" if ordinal is not None else "")
        key = (run_id, task_id, attempt) + (() if ordinal is None else (ordinal,))
        connection.execute(f"INSERT INTO {table}({columns},record_json,record_sha256) VALUES({','.join('?' for _ in range(len(key)+2))}) "
                           f"ON CONFLICT({columns}) DO UPDATE SET record_json=excluded.record_json,record_sha256=excluded.record_sha256",
                           (*key, _json(record), _digest(record)))
        kind = "quota.reservation" if ordinal is None else "quota.dispatch"
        self._event(connection, run_id=run_id, task_id=task_id, event_type=kind, created_at=now,
                    payload={"attempt": attempt, "ordinal": ordinal, "record": record, "record_sha256": _digest(record)},
                    event_key=f"{kind}:{task_id}:{attempt}:{ordinal}:{record['state']}")

    def _quota_records(self, connection, run_id, spec=None):
        try:
            return self._quota_records_verified(connection, run_id, spec)
        except QuotaError:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
            raise QuotaError("quota ledger/journal record is malformed") from exc

    def _quota_records_verified(self, connection, run_id, spec=None):
        spec = spec or self._quota_spec(connection, run_id)
        if spec.budget.execution_quota is None:
            return {}, {}
        events = self._replay_with_connection(connection, run_id)
        replayed_reservations, replayed_dispatches, _ = replay_quota(spec, run_id, events)
        latest = {}
        for event in events:
            if event["event_type"] in {"quota.reservation", "quota.dispatch"}:
                value = event["payload"]
                latest[(event["event_type"], event["task_id"], value["attempt"], value["ordinal"])] = value
        reservations, dispatches = {}, {}
        for table, kind, target in (("execution_reservations", "quota.reservation", reservations),
                                    ("execution_dispatches", "quota.dispatch", dispatches)):
            for row in connection.execute(f"SELECT * FROM {table} WHERE run_id=? ORDER BY task_id,attempt" + (",ordinal" if table.endswith("dispatches") else ""), (run_id,)):
                value = json.loads(row["record_json"])
                ordinal = row["ordinal"] if table.endswith("dispatches") else None
                linked = latest.pop((kind, row["task_id"], row["attempt"], ordinal), None)
                if (_digest(value) != row["record_sha256"] or linked != {"attempt": row["attempt"], "ordinal": ordinal, "record": value, "record_sha256": row["record_sha256"]}):
                    raise QuotaError("quota row differs from verified journal")
                key = (row["task_id"], row["attempt"])
                if ordinal is None:
                    if value["contract"] != reservation_contract(spec, run_id, *key) or value["state"] not in {"held", "settled", "unknown", "overrun"}:
                        raise QuotaError("quota reservation contract mismatch")
                    target[key] = value
                else:
                    target.setdefault(key, []).append(value)
        if latest:
            raise QuotaError("quota ledger rows are missing")
        for key, values in dispatches.items():
            if key not in reservations:
                raise QuotaError("dispatch has no reservation")
            for index, value in enumerate(values):
                if value["ordinal"] != index or value["state"] not in {"started", "measured", "unknown"}:
                    raise QuotaError("quota dispatch sequence invalid")
                expected = reservations[key]["contract"]["steps"][index]
                actual = value["request"]
                if any(actual[k] != expected[k] for k in ("label", "command_digest", "environment_sha256")) or not 0 < actual["timeout_ms"] <= expected["timeout_ms"] or not 0 < actual["output_cap_bytes"] <= expected["output_cap_bytes"]:
                    raise QuotaError("quota dispatch request differs from reserved command")
                if value["measurement"] is not None:
                    _vector(value["measurement"])
                    if value["state"] != "measured" or value["measurement"]["executor_calls"] != 1:
                        raise QuotaError("quota measurement state invalid")
                elif value["state"] == "measured":
                    raise QuotaError("quota measurement missing")
        if reservations != replayed_reservations or dispatches != replayed_dispatches:
            raise QuotaError("quota ledger differs from causal journal replay")
        return reservations, dispatches

    def _quota_check(self, connection, run_id, task_id, attempt):
        spec = self._quota_spec(connection, run_id)
        policy = spec.budget.execution_quota
        if policy is None:
            return None
        reservations, dispatches = self._quota_records(connection, run_id, spec)
        if len(reservations) >= MAX_CALLS:
            return "retention_limit"
        if any(item["state"] in {"unknown", "overrun"} for item in reservations.values()):
            return "uncertain_or_overrun"
        requested = reservation_contract(spec, run_id, task_id, attempt)
        owner = requested["owner_group"]
        for group, limit in ((None, policy["limits"]), (owner, policy["owners"].get(owner))):
            if limit is None:
                continue
            occupied = [_occupied(item, dispatches.get(key, [])) for key, item in reservations.items()
                        if group is None or item["contract"]["owner_group"] == group]
            if not _fits(_sum([*occupied, requested["reserved"]]), limit):
                return "run_limit" if group is None else "owner_limit"
        return None

    def _quota_reserve(self, connection, run_id, task_id, attempt, now):
        spec = self._quota_spec(connection, run_id)
        if spec.budget.execution_quota is None:
            return
        if self._quota_check(connection, run_id, task_id, attempt) is not None:
            raise QuotaError("quota reserve rejected")
        record = {"contract": reservation_contract(spec, run_id, task_id, attempt), "state": "held", "reason": None}
        self._quota_write(connection, run_id, task_id, attempt, record, now)

    def _quota_claim(self, connection, claim, now):
        from .store import LeaseLost
        self._approval_clock(connection, claim.run_id, now, update=True)
        self._approval_gate(connection, claim.run_id, claim.task_id, claim.attempt, now)
        row = connection.execute("SELECT * FROM tasks WHERE run_id=? AND task_id=?", (claim.run_id, claim.task_id)).fetchone()
        run = connection.execute("SELECT state,kill_switch FROM runs WHERE run_id=?", (claim.run_id,)).fetchone()
        if row is None or row["state"] != "running" or row["attempts"] != claim.attempt or row["lease_owner"] != claim.lease_owner or row["lease_expires_at"] <= now or run["state"] != "running" or run["kill_switch"]:
            raise LeaseLost("quota dispatch lease lost")
        reservations, dispatches = self._quota_records(connection, claim.run_id)
        key = (claim.task_id, claim.attempt)
        if key not in reservations:
            raise QuotaError("attempt reservation missing")
        return reservations[key], dispatches.get(key, [])

    def begin_dispatch(self, claim, request, ordinal):
        _integer(ordinal, MAX_CALLS)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            reservation, dispatches = self._quota_claim(connection, claim, now)
            all_reservations, _ = self._quota_records(connection, claim.run_id)
            if any(item['state'] in {'unknown', 'overrun'} for item in all_reservations.values()):
                raise QuotaError("run has uncertain/overrun consumption; new dispatch refused")
            if reservation["state"] != "held" or ordinal != len(dispatches) or any(item["state"] != "measured" for item in dispatches):
                raise QuotaError("dispatch is already started/settled or uncertain; re-execution refused")
            steps = reservation["contract"]["steps"]
            if ordinal >= len(steps):
                raise QuotaError("dispatch exceeds reservation")
            expected, actual = steps[ordinal], request_material(request)
            if any(actual[k] != expected[k] for k in ("label", "command_digest", "environment_sha256")) or actual["timeout_ms"] > expected["timeout_ms"] or actual["output_cap_bytes"] > expected["output_cap_bytes"]:
                raise QuotaError("dynamic request differs from reserved static command")
            record = {"ordinal": ordinal, "request": actual, "state": "started", "measurement": None, "measurement_origin": None,
                      "started_at": now, "finished_at": None}
            self._quota_write(connection, claim.run_id, claim.task_id, claim.attempt, record, now, ordinal)
            connection.commit()
            return _digest(record)
        except BaseException:
            connection.rollback(); raise
        finally:
            connection.close()

    def settle_dispatch(self, claim, ordinal, measurement, *, origin="caller_declared"):
        measurement = _vector(measurement)
        if not isinstance(origin, str) or origin not in {"caller_declared", "engine_monotonic_output"}:
            raise QuotaError("measurement origin invalid")
        if measurement["executor_calls"] != 1:
            raise QuotaError("one admitted executor invocation must be measured")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            reservation, dispatches = self._quota_claim(connection, claim, now)
            if type(ordinal) is not int or not 0 <= ordinal < len(dispatches):
                raise QuotaError("unknown dispatch")
            previous = dispatches[ordinal]
            if previous["state"] == "measured":
                if previous["measurement"] != measurement or previous["measurement_origin"] != origin:
                    raise QuotaError("dispatch already measured with different values")
                connection.commit(); return reservation["state"] != "overrun"
            if previous["state"] != "started" or reservation["state"] != "held":
                raise QuotaError("uncertain dispatch cannot be settled by stale worker")
            record = {**previous, "state": "measured", "measurement": measurement, "measurement_origin": origin, "finished_at": now}
            self._quota_write(connection, claim.run_id, claim.task_id, claim.attempt, record, now, ordinal)
            dispatches[ordinal] = record
            request = record["request"]
            within = (measurement["retained_output_bytes"] <= request["output_cap_bytes"] and measurement["execution_ms"] <= request["timeout_ms"] and _fits(_measured(dispatches), reservation["contract"]["reserved"]))
            if not within:
                self._quota_write(connection, claim.run_id, claim.task_id, claim.attempt,
                    {**reservation, "state": "overrun", "reason": "observed_execution_exceeded_reservation"}, now)
            connection.commit(); return within
        except BaseException:
            connection.rollback(); raise
        finally:
            connection.close()

    def fail_dispatch(self, claim):
        """An invocation raised without a complete measured result; keep reserve."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            self._quota_claim(connection, claim, now)
            self._quota_abandon(connection, claim.run_id, claim.task_id, claim.attempt, now)
            connection.commit()
        except BaseException:
            connection.rollback(); raise
        finally:
            connection.close()

    def _quota_abandon(self, connection, run_id, task_id, attempt, now):
        reservations, dispatches = self._quota_records(connection, run_id)
        key = (task_id, attempt)
        if key not in reservations:
            return False
        reservation, values = reservations[key], dispatches.get(key, [])
        if reservation["state"] in {"unknown", "overrun"}:
            return True
        if reservation["state"] == "settled":
            return False
        for value in values:
            if value["state"] == "started":
                self._quota_write(connection, run_id, task_id, attempt,
                    {**value, "state": "unknown", "finished_at": None}, now, value["ordinal"])
        self._quota_write(connection, run_id, task_id, attempt,
            {**reservation, "state": "unknown" if values else "settled",
             "reason": "interrupted_after_dispatch" if values else "never_dispatched"}, now)
        return bool(values)

    def quota_usage(self, claim):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            self._approval_clock(connection, claim.run_id, self.clock())
            reservations, dispatches = self._quota_records(connection, claim.run_id)
            key = (claim.task_id, claim.attempt)
            if key not in reservations:
                raise QuotaError("attempt reservation missing")
            return _receipt(reservations[key], dispatches.get(key, []))

    def _quota_complete(self, connection, claim, receipt, succeeded, now):
        spec = self._quota_spec(connection, claim.run_id)
        if spec.budget.execution_quota is None:
            return False
        reservation, dispatches = self._quota_claim(connection, claim, now)
        if any(item['state'] == 'started' for item in dispatches):
            raise QuotaError("mark uncertain dispatch before completing a failed attempt")
        expected = _receipt(reservation, dispatches)
        if _json(receipt.get("execution_quota")) != _json(expected):
            raise QuotaError("completion receipt differs from measured quota ledger")
        uncertain = not expected["measurement_complete"]
        overrun = reservation["state"] == "overrun"
        if succeeded:
            all_reservations, _ = self._quota_records(connection, claim.run_id)
            if any(item['state'] in {'unknown', 'overrun'} for item in all_reservations.values()):
                raise QuotaError("run has uncertain/overrun consumption; publication refused")
        if succeeded and (uncertain or overrun or len(dispatches) != len(reservation["contract"]["steps"])):
            raise QuotaError("quota evidence cannot authorize publication")
        if uncertain:
            self._quota_abandon(connection, claim.run_id, claim.task_id, claim.attempt, now)
        elif not overrun:
            self._quota_write(connection, claim.run_id, claim.task_id, claim.attempt,
                {**reservation, "state": "settled", "reason": "completion"}, now)
        return uncertain or overrun

    def _quota_snapshot(self, connection, run, tasks, now):
        spec = self._quota_spec(connection, run["run_id"])
        if spec.budget.execution_quota is None:
            return {}
        reservations, dispatches = self._quota_records(connection, run["run_id"], spec)
        waiting = [{"task_id": item["task_id"], "reason": reason} for item in tasks if item["state"] == "ready"
                   and (reason := self._quota_check(connection, run["run_id"], item["task_id"], item["attempts"]+1)) is not None]
        occupied = _sum(_occupied(value, dispatches.get(key, [])) for key, value in reservations.items())
        known = _sum(_measured(values) for values in dispatches.values())
        unknown = any(value["state"] in {"unknown", "overrun"} for value in reservations.values())
        value = {"limits": spec.budget.execution_quota, "occupied": occupied, "known_consumption": known,
                 "measurement_complete": not any(item["state"] in {"started", "unknown"} for values in dispatches.values() for item in values),
                 "reservation_states": [{"task_id": key[0], "attempt": key[1], "state": item["state"], "reason": item["reason"]} for key, item in reservations.items()],
                 "waiting": waiting, "uncertain_or_overrun": unknown,
                 "owner_authentication": "not_established", "tokens": None, "cost": None}
        value["consumption"] = known if value["measurement_complete"] else None
        output = {"execution_quota": value}
        ready_count = sum(item["state"] == "ready" for item in tasks)
        if ready_count and len(waiting) == ready_count and not any(item["state"] == "running" for item in tasks):
            output["execution_status"] = "waiting_quota"
        return output
