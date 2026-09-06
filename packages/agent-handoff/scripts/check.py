from pathlib import Path
import json
import py_compile


ROOT = Path(__file__).resolve().parents[1]
markers = [" ".join(("private", "repo", "name")), "_".join(("api", "key")) + "=", " ".join(("BEGIN", "PRIVATE", "KEY"))]
python_files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
for path in python_files:
    py_compile.compile(str(path), doraise=True)
json.loads((ROOT / "examples" / "handoff.json").read_text(encoding="utf-8"))
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".txt"}:
        continue
    text = path.read_text(encoding="utf-8").lower()
    if any(marker.lower() in text for marker in markers):
        raise SystemExit(f"public-boundary marker found in {path.relative_to(ROOT)}")
print(f"checked {len(python_files)} Python files and public fixtures")

