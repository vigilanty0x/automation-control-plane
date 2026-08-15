import argparse,hashlib,json
from pathlib import PurePosixPath
def analyze(worktrees):
 if not isinstance(worktrees,list) or len(worktrees)>100:return {"ok":False,"errors":["worktree_bound"]}
 names=[w.get("name") for w in worktrees if isinstance(w,dict)]
 if len(names)!=len(worktrees) or len(names)!=len(set(names)):return {"ok":False,"errors":["invalid_names"]}
 ownership={};errors=[]
 for w in worktrees:
  files=w.get("files",[])
  if not isinstance(files,list) or len(files)>5000:errors.append("file_bound");continue
  for path in files:
   if not isinstance(path,str) or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:errors.append("invalid_path");continue
   ownership.setdefault(path,[]).append(w["name"])
 if errors:return {"ok":False,"errors":errors}
 overlaps=[{"path":p,"worktrees":sorted(v)} for p,v in sorted(ownership.items()) if len(v)>1];pairs={}
 for x in overlaps:
  ws=x["worktrees"]
  for i,a in enumerate(ws):
   for b in ws[i+1:]:pairs[(a,b)]=pairs.get((a,b),0)+1
 lines=["flowchart LR"]+[f'  {a}["{a}"] ---|"{n} files"| {b}["{b}"]' for (a,b),n in sorted(pairs.items())];body={"overlaps":overlaps,"pairs":[{"left":a,"right":b,"files":n} for (a,b),n in sorted(pairs.items())],"mermaid":"\n".join(lines),"risk":"high" if len(overlaps)>10 else "medium" if overlaps else "low"};return {"ok":True,**body,"analysis_sha256":hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()}
def probe():
 g=analyze([{"name":"a","files":["x"]},{"name":"b","files":["x"]}]);b=analyze([{"name":"a","files":["../x"]}]);return {"ok":g["ok"] and len(g["overlaps"])==1 and not b["ok"],"path_counter_proof":not b["ok"]}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("command",choices=("analyze","probe"));p.add_argument("--input");a=p.parse_args(argv);o=probe() if a.command=="probe" else analyze(json.load(open(a.input))["worktrees"]);print(json.dumps(o,sort_keys=True));return 0 if o["ok"] else 2
