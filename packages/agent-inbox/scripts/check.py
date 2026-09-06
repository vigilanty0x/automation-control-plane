"""Dependency-free syntax and public-boundary checks."""

from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT = {".py", ".md", ".toml", ".json", ".yml", ".yaml"}
FORBIDDEN = ("sk" + "yom", "private" + "_token", "api" + "_key=", "authorization:" + " bearer")


def main() -> int:
    failures, inspected = [], 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in {"dist", "build", "__pycache__"} for part in path.parts): continue
        if path.suffix not in TEXT and path.name not in {"LICENSE", ".gitignore"}: continue
        inspected += 1; text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker in text.lower(): failures.append(f"{path.relative_to(ROOT)} contains forbidden boundary marker")
        if path.suffix == ".py":
            try: ast.parse(text, filename=str(path))
            except SyntaxError as exc: failures.append(f"{path.relative_to(ROOT)}: {exc}")
    for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "AI_ASSISTANCE.md", "CHANGELOG.md", "pyproject.toml", ".github/workflows/ci.yml"):
        if not (ROOT / name).is_file(): failures.append(f"missing required file: {name}")
    if failures: print("\n".join(failures), file=sys.stderr); return 1
    print(f"public-boundary: ok ({inspected} files inspected)"); return 0


if __name__ == "__main__": raise SystemExit(main())
