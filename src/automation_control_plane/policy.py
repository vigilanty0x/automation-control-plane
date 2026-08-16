"""Deny-by-default role and capability policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    "admin": ("*",),
    "operator": (
        "audit:read",
        "job:cancel",
        "job:read",
        "job:submit",
        "kill:read",
        "outbox:read",
        "trigger:manual",
        "trigger:scheduled",
        "trigger:webhook",
        "workflow:read",
        "workflow:register",
    ),
    "approver": ("approval:decide", "audit:read", "job:read", "workflow:read"),
    "dispatcher": ("outbox:deliver", "outbox:read"),
    "worker": (
        "handler:assert.equals",
        "handler:emit",
        "handler:json.merge",
        "handler:noop",
        "job:claim",
        "job:read",
        "workflow:read",
    ),
    "viewer": ("audit:read", "job:read", "kill:read", "outbox:read", "workflow:read"),
}


class AuthorizationError(PermissionError):
    """A principal lacks a required capability."""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    principal: str
    required: str
    matched: str | None


def capability_matches(granted: str, required: str) -> bool:
    if granted == "*" or granted == required:
        return True
    if granted.endswith(":*"):
        return required.startswith(granted[:-1])
    return False


def decide(principal: str, capabilities: Iterable[str], required: str) -> Decision:
    matched = next((item for item in capabilities if capability_matches(item, required)), None)
    return Decision(matched is not None, principal, required, matched)


def require(principal: str, capabilities: Iterable[str], required: str) -> None:
    decision = decide(principal, capabilities, required)
    if not decision.allowed:
        raise AuthorizationError(f"principal {principal!r} lacks capability {required!r}")
