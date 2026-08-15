from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = (ROOT / "src", ROOT / "tests", ROOT / "scripts")
MAX_LINE_LENGTH = 120
FORBIDDEN_PUBLIC_MARKERS = (
    "BEGIN OPENSSH " + "PRIVATE KEY",
    "BEGIN RSA " + "PRIVATE KEY",
    "gh" + "p_",
    "sk-" + "proj-",
)


def main() -> int:
    failures: list[str] = []
    python_files = sorted(
        path
        for root in PYTHON_ROOTS
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for path in python_files:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        try:
            ast.parse(text, filename=str(relative))
        except SyntaxError as exc:
            failures.append(f"{relative}: invalid syntax: {exc}")
        for number, line in enumerate(text.splitlines(), start=1):
            if line.rstrip() != line:
                failures.append(f"{relative}:{number}: trailing whitespace")
            if "\t" in line:
                failures.append(f"{relative}:{number}: tab character")
            if len(line) > MAX_LINE_LENGTH:
                failures.append(
                    f"{relative}:{number}: line length {len(line)} exceeds {MAX_LINE_LENGTH}"
                )
        for marker in FORBIDDEN_PUBLIC_MARKERS:
            if marker in text:
                failures.append(f"{relative}: forbidden public marker {marker!r}")

    for path in sorted((ROOT / "examples").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"checked {len(python_files)} Python files and public JSON fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
