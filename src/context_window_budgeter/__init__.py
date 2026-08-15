import argparse,hashlib,json
def budget(data):
 try:
  window=int(data["window_tokens"]);reserve=int(data["output_reserve"]);sections=data["sections"]
  if window<=0 or reserve<0 or reserve>=window or not isinstance(sections,list) or len(sections)>500:raise ValueError
 except (KeyError,TypeError,ValueError):return {"ok":False,"decision":"blocked","errors":["invalid_input"]}
 available=window-reserve;required=[s for s in sections if s.get("required")];optional=[s for s in sections if not s.get("required")]
 if any(not isinstance(s.get("tokens"),int) or s["tokens"]<0 for s in sections):return {"ok":False,"decision":"blocked","errors":["invalid_tokens"]}
 used=sum(s["tokens"] for s in required)
 if used>available:return {"ok":False,"decision":"blocked","errors":["required_overflow"],"required_tokens":used,"available":available}
 selected=[s["name"] for s in required];dropped=[]
 for s in sorted(optional,key=lambda x:(-int(x.get("priority",0)),x["name"])):
  if used+s["tokens"]<=available:selected.append(s["name"]);used+=s["tokens"]
  else:dropped.append(s["name"])
 body={"selected":selected,"dropped":dropped,"used":used,"available":available,"output_reserve":reserve};return {"ok":True,"decision":"ready" if not dropped else "degraded",**body,"plan_sha256":hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()}
def probe():
 g=budget({"window_tokens":10,"output_reserve":2,"sections":[{"name":"system","tokens":2,"required":True}]});b=budget({"window_tokens":4,"output_reserve":2,"sections":[{"name":"system","tokens":3,"required":True}]});return {"ok":g["ok"] and not b["ok"],"overflow_counter_proof":not b["ok"]}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("command",choices=("budget","probe"));p.add_argument("--input");a=p.parse_args(argv);o=probe() if a.command=="probe" else budget(json.load(open(a.input)));print(json.dumps(o,sort_keys=True));return 0 if o["ok"] else 2
