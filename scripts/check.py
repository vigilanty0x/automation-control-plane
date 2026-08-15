from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN=("BEGIN "+"PRIVATE KEY","gh"+"p_","api"+"_key=","pass"+"word=","/workspace/"+"scratch/")
def main():
    problems=[]
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py",".md",".json",".toml",".yml",".yaml",".txt"}: continue
        if any(p in {"build","__pycache__"} or p.endswith(".egg-info") for p in path.parts): continue
        text=path.read_text(encoding="utf-8")
        if any(marker.casefold() in text.casefold() for marker in FORBIDDEN): problems.append(f"{path.relative_to(ROOT)}: public-boundary marker")
        if path.suffix==".json":
            try: json.loads(text)
            except json.JSONDecodeError as exc: problems.append(f"{path.relative_to(ROOT)}: {exc}")
    for name in ("README.md","LICENSE","SECURITY.md","CONTRIBUTING.md","AI_ASSISTANCE.md","CHANGELOG.md","pyproject.toml",".github/workflows/ci.yml"):
        if not (ROOT/name).is_file(): problems.append(f"missing {name}")
    if problems: print("\n".join(problems),file=sys.stderr); return 1
    print("public-boundary and repository checks passed"); return 0
if __name__=="__main__": raise SystemExit(main())

