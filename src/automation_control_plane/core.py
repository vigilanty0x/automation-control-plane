"""Governed automation transitions with external identity and trusted state."""

import math

TRANSITIONS = {
    "pending": {"approved", "cancelled"},
    "approved": {"running", "cancelled"},
    "running": {"paused", "completed", "failed"},
    "paused": {"running", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
BASE_JOB_KEYS = {"id", "version", "action", "state", "spent", "budget"}
MAX_BUDGET = 1_000_000_000_000


def _text(value, label):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256:
        raise ValueError(f"{label} must be a bounded nonempty string")
    return value


def _amount(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= MAX_BUDGET:
        raise ValueError(f"{label} must be a finite nonnegative bounded number")
    return value


def _job(job):
    if not isinstance(job, dict) or not BASE_JOB_KEYS <= set(job) or set(job) - (BASE_JOB_KEYS | {"approval"}):
        raise ValueError("job has an invalid shape")
    _text(job["id"], "job id")
    _text(job["action"], "job action")
    if isinstance(job["version"], bool) or not isinstance(job["version"], int) or job["version"] < 1:
        raise ValueError("job version must be a positive integer")
    if job["state"] not in TRANSITIONS:
        raise ValueError("unknown job state")
    spent = _amount(job["spent"], "spent")
    budget = _amount(job["budget"], "budget")
    if spent > budget:
        raise ValueError("spent cannot exceed budget")
    if "approval" in job:
        _approval(job["approval"])


def _approval(approval):
    if not isinstance(approval, dict) or set(approval) != {"job_id", "version", "action", "approved_by"}:
        raise ValueError("approval has an invalid shape")
    _text(approval["job_id"], "approval job id")
    _text(approval["action"], "approval action")
    _text(approval["approved_by"], "approver")
    if isinstance(approval["version"], bool) or not isinstance(approval["version"], int) or approval["version"] < 1:
        raise ValueError("approval version must be a positive integer")


def _failure(job, reason):
    return {**job, "state": "failed", "reason": reason}


def transition(job, target, *, principal, capabilities, current_state, approval=None, kill_switch=False):
    _job(job)
    _text(target, "target")
    _text(principal, "principal")
    if not isinstance(capabilities, list) or len(capabilities) > 100 or len(capabilities) != len(set(capabilities)):
        raise ValueError("capabilities must be a bounded unique list")
    for capability in capabilities:
        _text(capability, "capability")
    if not isinstance(current_state, dict) or set(current_state) != {"id", "version", "state"}:
        raise ValueError("current_state has an invalid shape")
    _text(current_state["id"], "current state id")
    if isinstance(current_state["version"], bool) or not isinstance(current_state["version"], int) or current_state["version"] < 1 or current_state["state"] not in TRANSITIONS:
        raise ValueError("current_state contains invalid values")
    if not isinstance(kill_switch, bool):
        raise ValueError("kill_switch must be a boolean")
    if (current_state["id"], current_state["version"], current_state["state"]) != (job["id"], job["version"], job["state"]):
        return _failure(job, "stale_state")
    if kill_switch:
        if "kill" not in capabilities:
            return _failure(job, "kill_unauthorized")
        if job["state"] not in {"approved", "running", "paused"}:
            return _failure(job, "kill_not_applicable")
        return {**job, "state": "cancelled", "version": job["version"] + 1, "reason": "kill_switch"}
    if target not in TRANSITIONS[job["state"]]:
        return _failure(job, "invalid_transition")
    if target == "approved":
        if "approve" not in capabilities or approval is None:
            return _failure(job, "approval_missing")
        _approval(approval)
        expected = (job["id"], job["version"], job["action"], principal)
        supplied = (approval["job_id"], approval["version"], approval["action"], approval["approved_by"])
        if supplied != expected:
            return _failure(job, "approval_mismatch")
    elif "transition" not in capabilities:
        return _failure(job, "transition_unauthorized")
    if target == "running" and job["spent"] >= job["budget"]:
        return _failure(job, "budget_exhausted")
    result = {**job, "state": target, "version": job["version"] + 1}
    if target == "approved":
        result["approval"] = dict(approval)
    return result


def simulate(job, target):
    """Evaluate structural feasibility without making an authorization claim."""
    _job(job)
    _text(target, "target")
    reason = None
    would_transition = target in TRANSITIONS[job["state"]]
    if not would_transition:
        reason = "invalid_transition"
    elif target == "running" and job["spent"] >= job["budget"]:
        would_transition = False
        reason = "budget_exhausted"
    requirements = ["trusted_current_state", "authenticated_principal", "transition_capability"]
    if target == "approved":
        requirements = ["trusted_current_state", "authenticated_principal", "approve_capability", "bound_approval"]
    return {
        "status": "simulation_only",
        "authorization": "unverified",
        "job_id": job["id"],
        "job_version": job["version"],
        "current_state": job["state"],
        "target": target,
        "would_transition": would_transition,
        "reason": reason,
        "required_trust": requirements,
    }


def run(data):
    if not isinstance(data, dict) or set(data) != {"job", "target"}:
        raise ValueError("JSON input supports simulation only; trusted authorization context requires the programmatic transition boundary")
    return simulate(**data)
