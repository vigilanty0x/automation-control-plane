"""Bounded and fail-closed handoff contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STATES = {"queued", "running", "waiting", "failed", "rejected", "done"}
EVIDENCE_KINDS = {"commit", "test", "artifact", "decision", "log"}
MAX_TEXT = 10_000
MAX_ITEMS = 100


class ContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError(f"{name} must be an object")
    return raw


def _array(raw: Any, name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise ContractError(f"{name} must be an array")
    if len(raw) > MAX_ITEMS:
        raise ContractError(f"{name} exceeds {MAX_ITEMS} items")
    return raw


def _string(raw: Any, name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(f"{name} must be a non-empty string")
    if len(raw) > maximum:
        raise ContractError(f"{name} exceeds {maximum} characters")
    return raw


def _id(raw: Any, name: str) -> str:
    text = _string(raw, name, 64)
    if not ID_RE.fullmatch(text):
        raise ContractError(f"{name} has an invalid identifier")
    return text


def _timestamp(raw: Any, name: str) -> str:
    text = _string(raw, name, 40)
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{name} must be ISO-8601") from exc
    if value.tzinfo is None:
        raise ContractError(f"{name} must include timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    description: str
    met: bool

    @classmethod
    def from_dict(cls, raw: Any) -> "Criterion":
        data = _object(raw, "criterion")
        unknown = set(data) - {"criterion_id", "description", "met"}
        if unknown:
            raise ContractError(f"criterion has unknown fields: {sorted(unknown)}")
        if not isinstance(data.get("met"), bool):
            raise ContractError("criterion.met must be boolean")
        return cls(_id(data.get("criterion_id"), "criterion.criterion_id"), _string(data.get("description"), "criterion.description", 500), data["met"])

    def to_dict(self) -> dict[str, Any]:
        return {"criterion_id": self.criterion_id, "description": self.description, "met": self.met}


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: str
    uri: str
    sha256: str
    summary: str

    @classmethod
    def from_dict(cls, raw: Any) -> "Evidence":
        data = _object(raw, "evidence")
        unknown = set(data) - {"evidence_id", "kind", "uri", "sha256", "summary"}
        if unknown:
            raise ContractError(f"evidence has unknown fields: {sorted(unknown)}")
        kind = _string(data.get("kind"), "evidence.kind", 32)
        if kind not in EVIDENCE_KINDS:
            raise ContractError(f"evidence.kind must be one of {sorted(EVIDENCE_KINDS)}")
        sha = _string(data.get("sha256"), "evidence.sha256", 64)
        if not SHA_RE.fullmatch(sha):
            raise ContractError("evidence.sha256 must be lowercase SHA-256")
        return cls(
            _id(data.get("evidence_id"), "evidence.evidence_id"),
            kind,
            _string(data.get("uri"), "evidence.uri", 500),
            sha,
            _string(data.get("summary"), "evidence.summary", 500),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "kind": self.kind, "uri": self.uri, "sha256": self.sha256, "summary": self.summary}


@dataclass(frozen=True)
class OpenItem:
    item_id: str
    severity: str
    kind: str
    description: str

    @classmethod
    def from_dict(cls, raw: Any) -> "OpenItem":
        data = _object(raw, "open_item")
        unknown = set(data) - {"item_id", "severity", "kind", "description"}
        if unknown:
            raise ContractError(f"open_item has unknown fields: {sorted(unknown)}")
        severity = _string(data.get("severity"), "open_item.severity", 16)
        kind = _string(data.get("kind"), "open_item.kind", 16)
        if severity not in {"low", "medium", "high", "critical"}:
            raise ContractError("open_item.severity is invalid")
        if kind not in {"risk", "blocker", "disagreement", "escalation"}:
            raise ContractError("open_item.kind is invalid")
        return cls(_id(data.get("item_id"), "open_item.item_id"), severity, kind, _string(data.get("description"), "open_item.description", 1000))

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "severity": self.severity, "kind": self.kind, "description": self.description}


@dataclass(frozen=True)
class Handoff:
    schema_version: str
    handoff_id: str
    mission_id: str
    sequence: int
    state: str
    from_agent: str
    to_agent: str
    owner: str
    created_at: str
    path_scope: tuple[str, ...]
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    limits: dict[str, int]
    criteria: tuple[Criterion, ...]
    evidence: tuple[Evidence, ...]
    open_items: tuple[OpenItem, ...]
    summary: str

    @classmethod
    def from_dict(cls, raw: Any) -> "Handoff":
        data = _object(raw, "handoff")
        allowed = {
            "schema_version", "handoff_id", "mission_id", "sequence", "state",
            "from_agent", "to_agent", "owner", "created_at", "path_scope",
            "capabilities", "permissions", "limits", "criteria", "evidence",
            "open_items", "summary",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ContractError(f"handoff has unknown fields: {sorted(unknown)}")
        if data.get("schema_version") != "1.0":
            raise ContractError("handoff.schema_version must be 1.0")
        state = _string(data.get("state"), "handoff.state", 16)
        if state not in STATES:
            raise ContractError(f"handoff.state must be one of {sorted(STATES)}")
        sequence = data.get("sequence")
        if not isinstance(sequence, int) or not 0 <= sequence <= 1_000_000:
            raise ContractError("handoff.sequence must be an integer between 0 and 1000000")
        paths = tuple(sorted({_string(item, "handoff.path", 300) for item in _array(data.get("path_scope"), "handoff.path_scope")}))
        if not paths or any(path.startswith("/") or ".." in path.split("/") for path in paths):
            raise ContractError("handoff.path_scope must contain safe relative paths")
        capabilities = tuple(sorted({_id(item, "handoff.capability") for item in _array(data.get("capabilities", []), "handoff.capabilities")}))
        permissions = tuple(sorted({_id(item, "handoff.permission") for item in _array(data.get("permissions", []), "handoff.permissions")}))
        limits_raw = _object(data.get("limits"), "handoff.limits")
        limits: dict[str, int] = {}
        for key, value in limits_raw.items():
            name = _id(key, "handoff.limit")
            if not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
                raise ContractError(f"handoff limit {name} is invalid")
            limits[name] = value
        criteria = tuple(Criterion.from_dict(item) for item in _array(data.get("criteria"), "handoff.criteria"))
        evidence = tuple(Evidence.from_dict(item) for item in _array(data.get("evidence", []), "handoff.evidence"))
        open_items = tuple(OpenItem.from_dict(item) for item in _array(data.get("open_items", []), "handoff.open_items"))
        if not criteria:
            raise ContractError("handoff.criteria must not be empty")
        for label, values in (("criterion", [item.criterion_id for item in criteria]), ("evidence", [item.evidence_id for item in evidence]), ("open_item", [item.item_id for item in open_items])):
            if len(values) != len(set(values)):
                raise ContractError(f"handoff {label} IDs must be unique")
        if state == "done":
            if not evidence:
                raise ContractError("done requires machine-readable evidence")
            if any(not item.met for item in criteria):
                raise ContractError("done requires all criteria to be met")
            if any(item.kind in {"blocker", "escalation"} and item.severity in {"high", "critical"} for item in open_items):
                raise ContractError("done cannot hide high blockers or escalations")
        return cls(
            "1.0", _id(data.get("handoff_id"), "handoff.handoff_id"),
            _id(data.get("mission_id"), "handoff.mission_id"), sequence, state,
            _id(data.get("from_agent"), "handoff.from_agent"), _id(data.get("to_agent"), "handoff.to_agent"),
            _id(data.get("owner"), "handoff.owner"), _timestamp(data.get("created_at"), "handoff.created_at"),
            paths, capabilities, permissions, dict(sorted(limits.items())), criteria, evidence, open_items,
            _string(data.get("summary"), "handoff.summary", 2000),
        )

    @classmethod
    def from_json(cls, text: str) -> "Handoff":
        if len(text) > 1_000_000:
            raise ContractError("handoff JSON exceeds 1 MB")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(f"invalid JSON: {exc.msg}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "handoff_id": self.handoff_id,
            "mission_id": self.mission_id, "sequence": self.sequence, "state": self.state,
            "from_agent": self.from_agent, "to_agent": self.to_agent, "owner": self.owner,
            "created_at": self.created_at, "path_scope": list(self.path_scope),
            "capabilities": list(self.capabilities), "permissions": list(self.permissions),
            "limits": self.limits, "criteria": [item.to_dict() for item in self.criteria],
            "evidence": [item.to_dict() for item in self.evidence],
            "open_items": [item.to_dict() for item in self.open_items], "summary": self.summary,
        }

    @property
    def logical_sha256(self) -> str:
        return digest(self.to_dict())

