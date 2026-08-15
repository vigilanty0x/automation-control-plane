import argparse,hashlib,json
def build(data):
 required=("title","summary","completed","pending","evidence","risks","next_owner");missing=[x for x in required if not data.get(x)] if isinstance(data,dict) else list(required)
 if missing:return {"ok":False,"missing":missing}
 if any(not isinstance(data[x],list) for x in ("completed","pending","evidence","risks")) or not data["evidence"]:return {"ok":False,"missing":["evidence"]}
 lines=["# Handoff: "+data["title"],data["summary"],"Next owner: "+data["next_owner"]]
 for key,label in (("completed","Completed"),("pending","Pending"),("evidence","Evidence"),("risks","Risks")):lines.extend(["",f"## {label}"]+[f"- {x}" for x in data[key]])
 body="\n".join(lines);return {"ok":True,"markdown":body,"sha256":hashlib.sha256(body.encode()).hexdigest()}
def probe():
 g=build({"title":"d","summary":"s","completed":["c"],"pending":["p"],"evidence":["e"],"risks":["r"],"next_owner":"o"});b=build({"title":"d","summary":"s"});return {"ok":g["ok"] and not b["ok"],"incomplete_counter_proof":not b["ok"]}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("command",choices=("build","probe"));p.add_argument("--input");a=p.parse_args(argv);o=probe() if a.command=="probe" else build(json.load(open(a.input)));print(json.dumps(o,sort_keys=True));return 0 if o["ok"] else 2
