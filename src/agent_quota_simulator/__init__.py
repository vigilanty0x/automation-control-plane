import argparse,hashlib,json
def simulate(data):
 try:
  budget=data["budget"];tasks=data["tasks"]
  if not isinstance(tasks,list) or len(tasks)>1000:raise ValueError
  remaining={k:int(budget[k]) for k in ("tokens","seconds","cost_micros")}
  if min(remaining.values())<0:raise ValueError
 except (KeyError,TypeError,ValueError):return {"ok":False,"errors":["invalid_input"]}
 admitted=[];rejected=[]
 for t in sorted(tasks,key=lambda x:(-int(x.get("priority",0)),str(x.get("id")))):
  need={k:int(t.get(k,0)) for k in remaining}
  if any(v<0 for v in need.values()):return {"ok":False,"errors":["negative_demand"]}
  if all(need[k]<=remaining[k] for k in remaining):admitted.append(t["id"]);remaining={k:remaining[k]-need[k] for k in remaining}
  else:rejected.append({"id":t.get("id"),"reason":"quota"})
 body={"admitted":admitted,"rejected":rejected,"remaining":remaining};return {"ok":True,**body,"simulation_sha256":hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()}
def probe():
 g=simulate({"budget":{"tokens":10,"seconds":10,"cost_micros":10},"tasks":[{"id":"a","tokens":1,"seconds":1,"cost_micros":1}]});b=simulate({"budget":{"tokens":-1,"seconds":1,"cost_micros":1},"tasks":[]});return {"ok":g["ok"] and not b["ok"],"negative_counter_proof":not b["ok"]}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("command",choices=("simulate","probe"));p.add_argument("--input");a=p.parse_args(argv);o=probe() if a.command=="probe" else simulate(json.load(open(a.input)));print(json.dumps(o,sort_keys=True));return 0 if o["ok"] else 2
