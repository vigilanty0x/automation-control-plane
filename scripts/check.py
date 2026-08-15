from pathlib import Path
import ast,sys
r=Path(__file__).parents[1];f=[];m="sk"+"yom"
for p in r.rglob("*.py"):
 try:ast.parse(p.read_text())
 except SyntaxError as e:f.append(str(e))
for p in r.rglob("*"):
 if p.is_file() and p.suffix in {".py",".md",".toml",".yml"} and m in p.read_text().lower():f.append(str(p))
print("public-boundary: ok" if not f else f);sys.exit(bool(f))
