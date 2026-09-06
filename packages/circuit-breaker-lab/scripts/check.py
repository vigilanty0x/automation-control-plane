from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN=("BEGIN "+"PRIVATE KEY","gh"+"p_","api"+"_key=","/workspace/"+"scratch/")
def main():
 problems=[]
 for path in ROOT.rglob("*"):
  if path.is_file() and path.suffix in {".py",".md",".toml",".yml",".yaml",".txt"} and not any(x in path.parts for x in ("build","__pycache__")):
   text=path.read_text(encoding="utf-8")
   if any(x.casefold() in text.casefold() for x in FORBIDDEN): problems.append(str(path))
 required=("README.md","LICENSE","pyproject.toml",".github/workflows/ci.yml")
 problems += [x for x in required if not (ROOT/x).is_file()]
 if problems: print("\n".join(problems),file=sys.stderr); return 1
 print("public-boundary and repository checks passed"); return 0
if __name__=="__main__": raise SystemExit(main())

