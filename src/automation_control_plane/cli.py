import json,sys
from pathlib import Path
from .core import run
def main():
 try:
  data=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if len(sys.argv)>1 else json.load(sys.stdin)
  print(json.dumps({"success":True,"result":run(data)},sort_keys=True)); raise SystemExit(0)
 except Exception as exc:
  print(json.dumps({"success":False,"error":type(exc).__name__,"message":str(exc)},sort_keys=True)); raise SystemExit(2)
if __name__=="__main__": main()

