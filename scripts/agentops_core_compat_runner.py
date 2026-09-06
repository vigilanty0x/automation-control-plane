from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import agentops_core_compat_probe as probe


def _safe_load_module(path: Path, name: str) -> ModuleType:
    """Load an exact-source probe module with dataclass-safe module registration."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


probe._load_module = _safe_load_module


if __name__ == "__main__":
    raise SystemExit(probe.main())
