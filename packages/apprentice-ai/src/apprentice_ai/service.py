"""Application service composing the auditable reference workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import run_benchmarks
from .contracts import CONTRACT_NAMES, SPEC_VERSION
from .localfs import secure_directory
from .learning import apply_answer, discover_routine, generate_question, segment_sessions
from .learnpack import export_learnpack, import_learnpack
from .skills import compile_skill, preview_skill
from .store import EventStore
from .synthetic import seed_synthetic_office


def ensure_data_dir(path: str | Path) -> Path:
    return secure_directory(path)


def database_path(data_dir: str | Path) -> Path:
    return ensure_data_dir(data_dir) / "apprentice.sqlite"


def capabilities() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "mode": "local_first_preview_only",
        "runtime_dependencies": [],
        "capture_adapters": {
            "synthetic-office/0.1.0": "available",
            "jsonl-import/0.1.0": "available",
            "windows-native": "not_implemented_not_claimed",
        },
        "model_providers": {"deterministic-local-baseline": "available", "cloud": "disabled"},
        "execution": {"preview": True, "real_actions": False, "shell": False, "network": False},
        "contracts": list(CONTRACT_NAMES),
        "learnpack": {"export": True, "quarantined_import": True, "execution": False},
    }


def run_reference_demo(
    data_dir: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    root = ensure_data_dir(data_dir)
    pack_path = Path(output) if output is not None else root / "reference-normalization.learnpack"
    with EventStore(root / "apprentice.sqlite") as store:
        profile_id = store.create_profile("Synthetic reference builder")
        seed = seed_synthetic_office(store, profile_id)
        episodes = segment_sessions(store, profile_id)
        routine = discover_routine(store, profile_id)
        question = generate_question(store, profile_id, routine["routine_id"])
        answer = apply_answer(
            store,
            profile_id,
            question["id"],
            "yes",
            explanation="Synthetic reference confirmation.",
            synthetic=True,
        )
        skill = compile_skill(store, profile_id, routine["routine_id"])
        preview = preview_skill(skill, {"source_dataset": "fixture://D6"})
        exported = export_learnpack(
            store, profile_id, skill["skill_id"], skill["version"], pack_path
        )
        blank_profile = store.create_profile("Blank import profile")
        imported = import_learnpack(store, blank_profile, pack_path)
        benchmark = run_benchmarks(store, profile_id, canary_receipts=seed["canary_receipt"])
        routine = store.get_routine(profile_id, routine["routine_id"])
        _, question = store.get_question(profile_id, question["id"])
    return {
        "status": "success_proved" if benchmark["all_required_passed"] else "unknown",
        "profile_id": profile_id,
        "blank_profile_id": blank_profile,
        "seed": seed,
        "episodes": {"created": len(episodes), "ids": [item["episode_id"] for item in episodes]},
        "routine": routine,
        "question": question,
        "answer": answer,
        "skill": {"skill_id": skill["skill_id"], "version": skill["version"]},
        "preview": preview,
        "export": exported,
        "import": imported,
        "benchmark": benchmark,
    }


def prepare_reference_observation(data_dir: str | Path) -> dict[str, Any]:
    """Build D1-D5 evidence and stop at the explicit human-decision gate."""

    root = ensure_data_dir(data_dir)
    with EventStore(root / "apprentice.sqlite") as store:
        profile_id = store.create_profile("Synthetic guided review")
        seed = seed_synthetic_office(store, profile_id)
        episodes = segment_sessions(store, profile_id)
        routine = discover_routine(store, profile_id)
        question = generate_question(store, profile_id, routine["routine_id"])
    return {
        "status": "awaiting_human_answer",
        "profile_id": profile_id,
        "seed": seed,
        "episodes": {"created": len(episodes), "ids": [item["episode_id"] for item in episodes]},
        "routine": routine,
        "question": question,
        "next_actions": ["answer_question", "dismiss", "snooze"],
        "execution_supported": False,
    }
