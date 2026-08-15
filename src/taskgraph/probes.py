from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

from . import __version__
from .engine import TaskGraphEngine
from .models import ContractError, Evidence, GraphSpec
from .store import TaskStore


def liveness_probe() -> dict[str, Any]:
    return {"ok": True, "probe": "liveness", "version": __version__}


def readiness_probe() -> dict[str, Any]:
    return {"ok": True, "probe": "readiness", "schema_versions": ["1.0"], "runtime_dependencies": [], "store": "sqlite3"}


def fixture() -> GraphSpec:
    return GraphSpec.from_dict({
        "schema_version": "1.0", "graph_id": "functional-probe", "version": "1.0.0",
        "tasks": [
            {"task_id": "build", "owner": "builder", "description": "Build synthetic artifact", "path_scope": ["src/demo.py"], "dependencies": [], "max_attempts": 2, "required_evidence": ["test"]},
            {"task_id": "review", "owner": "reviewer", "description": "Review synthetic artifact", "path_scope": ["tests/test_demo.py"], "dependencies": ["build"], "max_attempts": 1, "required_evidence": ["decision"]},
        ],
    })


def functional_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        store = TaskStore(Path(directory) / "graph.db")
        engine = TaskGraphEngine(store)
        graph = fixture()
        engine.register(graph)
        build = engine.claim(graph.graph_id, "worker-a", 100, 10)
        counter_failed = False
        try:
            engine.complete(graph.graph_id, "build", "worker-a", [], {}, "bad-completion")
        except ContractError:
            counter_failed = True
        evidence = [Evidence("test", "artifact://tests", "1" * 64)]
        engine.complete(graph.graph_id, "build", "worker-a", evidence, {"tests": "passed"}, "complete-build")
        review = engine.claim(graph.graph_id, "worker-b", 110, 10)
        final = engine.snapshot(graph.graph_id)
        store.close()
    return {
        "ok": bool(build and review and counter_failed and final["counts"]["done"] == 1),
        "probe": "functional", "control_claimed": bool(build), "dependency_unlocked": bool(review),
        "counter_example_failed": counter_failed, "done_requires_evidence": counter_failed,
    }

