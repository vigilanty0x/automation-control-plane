"""Privacy guard that runs before any event reaches durable storage."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .errors import ValidationError

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "GENERIC_SECRET",
        re.compile(
            r"(?i)\b(api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*[^\s,;]{6,}"
        ),
    ),
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("WINDOWS_USER_PATH", re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+")),
    ("POSIX_USER_PATH", re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s]+")),
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "authorization",
        "cookie",
        "set_cookie",
        "credential",
        "credentials",
        "otp",
        "one_time_code",
        "private_key",
    }
)
_EVENT_KEYS = frozenset({"event_id", "timestamp", "source", "application", "action", "context"})
_APPLICATION_KEYS = frozenset({"id", "version"})
_ACTION_KEYS = frozenset({"kind", "target_role", "target_label", "value", "text", "content"})
_CONTEXT_KEYS = frozenset(
    {
        "demo_id",
        "dataset_id",
        "climate",
        "split",
        "synthetic",
        "url",
        "password",
        "token",
        "api_key",
        "credential",
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")


@dataclass(slots=True, frozen=True)
class PrivacyPolicy:
    denied_applications: frozenset[str] = frozenset(
        {"password-manager", "banking-app", "authenticator", "synthetic-secret-vault"}
    )
    denied_domains: frozenset[str] = frozenset()
    sensitive_field_roles: frozenset[str] = frozenset(
        {"password", "new-password", "one-time-code", "credit-card-number", "secret"}
    )
    max_string_length: int = 20_000
    max_collection_items: int = 10_000


@dataclass(slots=True, frozen=True)
class PrivacyDecision:
    allowed: bool
    reason_code: str
    event: dict[str, Any] | None
    redactions: tuple[str, ...] = field(default_factory=tuple)
    privacy_class: str = "D1"


class PrivacyGuard:
    def __init__(self, policy: PrivacyPolicy | None = None) -> None:
        self.policy = policy or PrivacyPolicy()

    def sanitize_event(self, raw: dict[str, Any]) -> PrivacyDecision:
        if not isinstance(raw, dict):
            raise ValidationError("event must be an object")
        if len(raw) > 100:
            raise ValidationError("event has too many fields")
        self._validate_event_contract(raw)
        app = raw.get("application", {})
        if not isinstance(app, dict):
            raise ValidationError("application must be an object")
        app_id = str(app.get("id", "unknown")).strip().casefold()
        if app_id in {item.casefold() for item in self.policy.denied_applications}:
            return PrivacyDecision(False, "DENY_APPLICATION", None, (), "D4")
        domain = self._event_domain(raw)
        if domain and self._domain_denied(domain):
            return PrivacyDecision(False, "DENY_DOMAIN", None, (), "D4")

        action = raw.get("action", {})
        if not isinstance(action, dict):
            raise ValidationError("action must be an object")
        role = str(action.get("target_role", "")).casefold()
        sanitized = copy.deepcopy(raw)
        categories: set[str] = set()
        if role in self.policy.sensitive_field_roles:
            sanitized_action = sanitized.setdefault("action", {})
            for key in ("value", "text", "content", "target_label"):
                if key in sanitized_action:
                    sanitized_action[key] = "[REDACTED:SENSITIVE_FIELD]"
            categories.add("SENSITIVE_FIELD")

        sanitized = self._redact_tree(sanitized, categories, depth=0)
        privacy_class = "D2" if categories else "D1"
        privacy = sanitized.setdefault("privacy", {})
        if not isinstance(privacy, dict):
            raise ValidationError("privacy must be an object")
        privacy["classification"] = privacy_class
        privacy["redactions"] = sorted(categories)
        privacy["guard"] = "privacy-guard/0.1.0"
        return PrivacyDecision(
            True,
            "ALLOW_REDACTED" if categories else "ALLOW_MINIMAL",
            sanitized,
            tuple(sorted(categories)),
            privacy_class,
        )

    def _validate_event_contract(self, raw: dict[str, Any]) -> None:
        if any(not isinstance(key, str) for key in raw):
            raise ValidationError("event object keys must be strings")
        unknown = set(raw) - _EVENT_KEYS
        if unknown:
            raise ValidationError(f"event contains unsupported fields: {', '.join(sorted(map(str, unknown)))}")
        source = raw.get("source")
        if source is not None and (not isinstance(source, str) or not _IDENTIFIER.fullmatch(source)):
            raise ValidationError("event source must be a bounded adapter identifier")
        application = raw.get("application")
        if not isinstance(application, dict):
            raise ValidationError("application must be an object")
        if any(not isinstance(key, str) for key in application):
            raise ValidationError("application object keys must be strings")
        app_unknown = set(application) - _APPLICATION_KEYS
        if app_unknown:
            raise ValidationError(
                f"application contains unsupported fields: {', '.join(sorted(map(str, app_unknown)))}"
            )
        if not isinstance(application.get("id"), str) or not _IDENTIFIER.fullmatch(application["id"]):
            raise ValidationError("application id must be a bounded identifier")
        version = application.get("version")
        if version is not None and (not isinstance(version, str) or not _IDENTIFIER.fullmatch(version)):
            raise ValidationError("application version must be a bounded identifier")
        action = raw.get("action")
        if not isinstance(action, dict):
            raise ValidationError("action must be an object")
        if any(not isinstance(key, str) for key in action):
            raise ValidationError("action object keys must be strings")
        action_unknown = set(action) - _ACTION_KEYS
        if action_unknown:
            raise ValidationError(f"action contains unsupported fields: {', '.join(sorted(map(str, action_unknown)))}")
        for name in ("kind", "target_role"):
            value = action.get(name)
            if value is not None and (not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)):
                raise ValidationError(f"action {name} must be a bounded identifier")
        label = action.get("target_label")
        if label is not None and (not isinstance(label, str) or len(label) > 240):
            raise ValidationError("target_label must be at most 240 characters")
        role = str(action.get("target_role", "")).casefold()
        content_keys = {name for name in ("value", "text", "content") if name in action}
        app_is_denied = str(application.get("id", "")).casefold() in {
            item.casefold() for item in self.policy.denied_applications
        }
        if content_keys and role not in self.policy.sensitive_field_roles and not app_is_denied:
            raise ValidationError("raw action content is forbidden; persist only semantic references")
        context = raw.get("context", {})
        if not isinstance(context, dict):
            raise ValidationError("context must be an object")
        if any(not isinstance(key, str) for key in context):
            raise ValidationError("context object keys must be strings")
        context_unknown = set(context) - _CONTEXT_KEYS
        if context_unknown:
            raise ValidationError(f"context contains unsupported fields: {', '.join(sorted(map(str, context_unknown)))}")
        for name, value in context.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
            if normalized in _SENSITIVE_KEYS:
                continue
            if name == "synthetic":
                if type(value) is not bool:
                    raise ValidationError("context synthetic must be boolean")
            elif name == "url":
                if not isinstance(value, str) or len(value) > 2048:
                    raise ValidationError("context url is invalid")
            elif not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValidationError(f"context {name} must be a bounded identifier")

    def scan_text(self, text: str) -> tuple[str, tuple[str, ...]]:
        categories: set[str] = set()
        return self._redact_string(text, categories), tuple(sorted(categories))

    def sanitize_payload(self, value: Any) -> tuple[Any, tuple[str, ...]]:
        """Redact an arbitrary JSON-compatible payload before persistence."""
        categories: set[str] = set()
        sanitized = self._redact_tree(copy.deepcopy(value), categories, depth=0)
        return sanitized, tuple(sorted(categories))

    def _redact_tree(self, value: Any, categories: set[str], *, depth: int) -> Any:
        if depth > 32:
            raise ValidationError("event nesting limit exceeded")
        if isinstance(value, str):
            if len(value) > self.policy.max_string_length:
                raise ValidationError("event string limit exceeded")
            return self._redact_string(value, categories)
        if isinstance(value, list):
            if len(value) > self.policy.max_collection_items:
                raise ValidationError("event list limit exceeded")
            return [self._redact_tree(item, categories, depth=depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > self.policy.max_collection_items:
                raise ValidationError("event object limit exceeded")
            if any(not isinstance(key, str) for key in value):
                raise ValidationError("event object keys must be strings")
            result: dict[str, Any] = {}
            for key, item in value.items():
                if len(key) > 200 or any(ord(character) < 32 for character in key):
                    raise ValidationError("event object key is invalid")
                normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
                if normalized in _SENSITIVE_KEYS:
                    categories.add("SENSITIVE_KEY")
                    result[key] = "[REDACTED:SENSITIVE_KEY]"
                else:
                    result[key] = self._redact_tree(item, categories, depth=depth + 1)
            return result
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise ValidationError(f"unsupported event value type: {type(value).__name__}")

    @staticmethod
    def _redact_string(value: str, categories: set[str]) -> str:
        result = value
        for category, pattern in _PATTERNS:
            if pattern.search(result):
                categories.add(category)
                result = pattern.sub(f"[REDACTED:{category}]", result)
        return result

    @staticmethod
    def _event_domain(raw: dict[str, Any]) -> str | None:
        context = raw.get("context", {})
        if not isinstance(context, dict):
            return None
        url = context.get("url")
        if not isinstance(url, str) or not url:
            return None
        try:
            return (urlsplit(url).hostname or "").casefold().rstrip(".") or None
        except ValueError:
            return None

    def _domain_denied(self, domain: str) -> bool:
        for denied in self.policy.denied_domains:
            candidate = denied.casefold().strip(".")
            if domain == candidate or domain.endswith(f".{candidate}"):
                return True
        return False
