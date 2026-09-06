"""Versioned contracts shared by the store, learning pipeline and interfaces."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .errors import ValidationError

SPEC_VERSION = "0.1.0"
ID_RE = re.compile(r"^[a-z]{3}_[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    POLICY = "policy"
    NEGATIVE = "negative"
    PROVENANCE = "provenance"


class RoutineStatus(StrEnum):
    OBSERVED = "observed"
    EXPLAINED = "explained"
    CONFIRMED = "confirmed"
    COMPILABLE = "compilable"
    REJECTED = "rejected"


class QuestionStatus(StrEnum):
    CANDIDATE = "candidate"
    QUEUED = "queued"
    SHOWN = "shown"
    SNOOZED = "snoozed"
    ANSWERED = "answered"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class TrustState(StrEnum):
    DISABLED_UNTRUSTED = "disabled_untrusted"
    VALIDATED_LOCAL = "validated_local"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def require_id(value: str, *, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValidationError(f"invalid identifier: {value!r}")
    if prefix is not None and not value.startswith(f"{prefix}_"):
        raise ValidationError(f"identifier must use {prefix}_ prefix")
    return value


def require_text(value: Any, name: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be non-empty text")
    if len(value) > maximum:
        raise ValidationError(f"{name} exceeds {maximum} characters")
    return value.strip()


@dataclass(slots=True, frozen=True)
class PermissionManifest:
    filesystem_read: tuple[str, ...] = ()
    filesystem_write: tuple[str, ...] = ()
    filesystem_delete: tuple[str, ...] = ()
    network_mode: str = "deny"
    applications_activate: tuple[str, ...] = ()
    ui_click: bool = False
    ui_type_text: bool = False
    clipboard: bool = False
    external_effects: dict[str, bool] = field(default_factory=dict)
    max_actions: int = 50
    max_duration_seconds: int = 180
    max_model_calls: int = 0
    max_retries: int = 1

    def validate(self) -> None:
        if self.network_mode not in {"deny", "allowlist"}:
            raise ValidationError("network_mode must be deny or allowlist")
        if self.filesystem_delete:
            raise ValidationError("filesystem delete is forbidden in release 0.1.0")
        if any(self.external_effects.values()):
            raise ValidationError("external effects are forbidden in release 0.1.0")
        for value, label, upper in (
            (self.max_actions, "max_actions", 10_000),
            (self.max_duration_seconds, "max_duration_seconds", 86_400),
            (self.max_model_calls, "max_model_calls", 100),
            (self.max_retries, "max_retries", 20),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
                raise ValidationError(f"{label} is outside its safe range")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "version": SPEC_VERSION,
            "filesystem": {
                "read": list(self.filesystem_read),
                "write": list(self.filesystem_write),
                "delete": list(self.filesystem_delete),
            },
            "network": {"mode": self.network_mode},
            "applications": {"activate": list(self.applications_activate)},
            "ui": {
                "click": self.ui_click,
                "type_text": self.ui_type_text,
                "clipboard": self.clipboard,
            },
            "external_effects": dict(sorted(self.external_effects.items())),
            "budgets": {
                "max_actions": self.max_actions,
                "max_duration_seconds": self.max_duration_seconds,
                "max_model_calls": self.max_model_calls,
                "max_retries": self.max_retries,
            },
            "human_confirmation": {
                "before_first_run": True,
                "before_irreversible_effect": "always",
            },
        }


@dataclass(slots=True, frozen=True)
class Evidence:
    evidence_id: str
    kind: str
    claim: str
    observer_type: str
    observed_at: str
    inputs: dict[str, Any]
    result: dict[str, Any]
    digest: str
    confidence: str = "proved"

    def to_dict(self) -> dict[str, Any]:
        require_id(self.evidence_id, prefix="evd")
        if self.confidence not in {"proved", "failed", "unknown"}:
            raise ValidationError("invalid evidence confidence")
        return {
            "evidence_id": self.evidence_id,
            "kind": require_text(self.kind, "kind", maximum=80),
            "claim": require_text(self.claim, "claim", maximum=1000),
            "observer": {"type": require_text(self.observer_type, "observer_type", maximum=120)},
            "observed_at": self.observed_at,
            "inputs": self.inputs,
            "result": self.result,
            "integrity": {"digest": self.digest},
            "confidence": self.confidence,
        }


CONTRACT_NAMES = (
    "EventInput",
    "EventEnvelope",
    "Episode",
    "RoutineCandidate",
    "Question",
    "MemoryAssertion",
    "SkillIR",
    "PermissionManifest",
    "Evidence",
    "LearnPackManifest",
)
