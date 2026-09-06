from pathlib import Path
import json
import py_compile

ROOT = Path(__file__).resolve().parents[1]
markers = [" ".join(("private", "repo", "name")), "_".join(("api", "key")) + "=", " ".join(("BEGIN", "PRIVATE", "KEY"))]
files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
for path in files: py_compile.compile(str(path), doraise=True)
for path in (ROOT / "examples").glob("*.json"): json.loads(path.read_text(encoding="utf-8"))
for path in ROOT.rglob("*"):
    if path.is_file() and path.suffix.lower() in {".py", ".md", ".json", ".toml", ".yml", ".txt"}:
        text = path.read_text(encoding="utf-8").lower()
        if any(marker.lower() in text for marker in markers): raise SystemExit(f"public-boundary marker found in {path.relative_to(ROOT)}")
print(f"checked {len(files)} Python files and public fixtures")

