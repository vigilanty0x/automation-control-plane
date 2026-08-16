"""Deterministic Synthetic Office demonstrations D1-D5."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .privacy import PrivacyGuard
from .store import EventStore


@dataclass(slots=True, frozen=True)
class Demonstration:
    demo_id: str
    climate: str
    split: str
    actions: tuple[str, ...]


DEMONSTRATIONS: tuple[Demonstration, ...] = (
    Demonstration(
        "D1",
        "tropical",
        "induction",
        ("export_data", "open_file", "correct_humidity", "normalize_units", "save_output"),
    ),
    Demonstration(
        "D2",
        "tropical",
        "induction",
        (
            "export_data",
            "open_file",
            "acknowledge_warning",
            "correct_humidity",
            "normalize_units",
            "save_output",
        ),
    ),
    Demonstration(
        "D3",
        "temperate",
        "induction",
        ("export_data", "open_file", "normalize_units", "save_output"),
    ),
    Demonstration(
        "D4",
        "tropical",
        "holdout",
        ("export_data", "open_file", "normalize_units", "correct_humidity", "save_output"),
    ),
    Demonstration(
        "D5",
        "temperate",
        "holdout",
        ("export_data", "open_file", "normalize_units", "save_output"),
    ),
)

ACTION_LABELS = {
    "task_start": "Commencer le nettoyage",
    "export_data": "Exporter les mesures",
    "open_file": "Ouvrir le fichier",
    "acknowledge_warning": "Acquitter l’avertissement",
    "correct_humidity": "Appliquer la correction d’humidité",
    "normalize_units": "Normaliser les unités",
    "save_output": "Enregistrer le résultat",
    "task_end": "Terminer le nettoyage",
}

CANARY_CATALOG: dict[str, bytes] = {
    "redacted-target-label": b"TEST-SECRET-ONLY-12345",
    "blocked-denied-application": b"SYNTHETIC-DO-NOT-STORE",
}


def canary_receipt(canary_id: str) -> dict[str, str]:
    value = CANARY_CATALOG[canary_id]
    return {
        "canary_id": canary_id,
        "digest": f"sha256:{hashlib.sha256(value).hexdigest()}",
    }


def seed_synthetic_office(
    store: EventStore,
    profile_id: str,
    guard: PrivacyGuard | None = None,
) -> dict[str, Any]:
    privacy = guard or PrivacyGuard()
    sessions: list[str] = []
    receipts: list[dict[str, str]] = []
    persisted = 0
    blocked = 0
    redacted = 0
    namespace = profile_id.removeprefix("pro_")[:12]
    for index, demo in enumerate(DEMONSTRATIONS, start=1):
        session_id = store.create_session(
            profile_id,
            mode="synthetic",
            source="synthetic-office/0.1.0",
            metadata={
                "demo_id": demo.demo_id,
                "climate": demo.climate,
                "split": demo.split,
                "goal": "normalize_lab_export",
                "effect": "normalized_export_saved",
                "synthetic": True,
            },
            session_id=f"ses_{namespace}_{demo.demo_id}",
        )
        sessions.append(session_id)
        actions = ("task_start",) + demo.actions + ("task_end",)
        for offset, action in enumerate(actions):
            event: dict[str, Any] = {
                "event_id": f"evt_{namespace}_{demo.demo_id}_{offset + 1:03d}",
                "timestamp": f"2026-08-{index + 1:02d}T10:{offset:02d}:00Z",
                "source": "synthetic-office",
                "application": {
                    "id": "synthetic-lab" if action in {"task_start", "export_data"} else "synthetic-sheet",
                    "version": "1.0.0",
                },
                "action": {
                    "kind": action,
                    "target_role": "button" if action not in {"open_file", "normalize_units"} else "document",
                    "target_label": ACTION_LABELS[action],
                },
                "context": {
                    "demo_id": demo.demo_id,
                    "dataset_id": f"dataset-{demo.demo_id.lower()}",
                    "climate": demo.climate,
                    "split": demo.split,
                    "synthetic": True,
                },
            }
            if demo.demo_id == "D1" and action == "open_file":
                event["action"]["target_label"] = "api_key=TEST-SECRET-ONLY-12345"
                receipt = canary_receipt("redacted-target-label")
                store.record_audit(
                    profile_id,
                    component="benchmark",
                    action="canary_attempt",
                    reason_code="SYNTHETIC_CANARY",
                    details=receipt,
                )
                receipts.append(receipt)
            saved = store.append_event(profile_id, session_id, event, privacy)
            if saved is None:
                blocked += 1
            else:
                persisted += 1
                if saved.get("privacy", {}).get("redactions"):
                    redacted += 1
        if demo.demo_id == "D2":
            denied_event = {
                "event_id": f"evt_{namespace}_D2_vault",
                "timestamp": "2026-08-03T10:58:00Z",
                "source": "synthetic-office",
                "application": {"id": "synthetic-secret-vault", "version": "1.0.0"},
                "action": {
                    "kind": "activate_control",
                    "target_role": "password",
                    "value": "SYNTHETIC-DO-NOT-STORE",
                },
                "context": {"synthetic": True},
            }
            receipt = canary_receipt("blocked-denied-application")
            store.record_audit(
                profile_id,
                component="benchmark",
                action="canary_attempt",
                reason_code="SYNTHETIC_CANARY",
                details=receipt,
            )
            receipts.append(receipt)
            if store.append_event(profile_id, session_id, denied_event, privacy) is None:
                blocked += 1
        store.end_session(profile_id, session_id)
    return {
        "profile_id": profile_id,
        "sessions": sessions,
        "demonstrations": len(DEMONSTRATIONS),
        "events_persisted": persisted,
        "events_blocked": blocked,
        "events_redacted": redacted,
        "canary_receipt": receipts,
        "trace_determinism": "logical_actions_only; generated identifiers differ per profile",
    }
