import argparse,hashlib,json
def record(events):
 if not isinstance(events,list) or len(events)>10000:return {"ok":False,"errors":["event_bound"]}
 chain=[];previous="0"*64;seen=set()
 for expected,e in enumerate(events,1):
  if not isinstance(e,dict) or e.get("sequence")!=expected or expected in seen or e.get("kind") not in {"input","output","tool","decision","error"}:return {"ok":False,"errors":["invalid_sequence_or_kind"]}
  seen.add(expected);body={"sequence":expected,"kind":e["kind"],"content":e.get("content"),"previous_sha256":previous};digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest();chain.append({**body,"event_sha256":digest});previous=digest
 return {"ok":True,"events":chain,"head_sha256":previous,"count":len(chain)}
def verify(transcript):
 rebuilt=record([{"sequence":e.get("sequence"),"kind":e.get("kind"),"content":e.get("content")} for e in transcript.get("events",[])]);return {"ok":rebuilt["ok"] and rebuilt.get("head_sha256")==transcript.get("head_sha256")}
def probe():
 g=record([{"sequence":1,"kind":"input","content":"demo"}]);b=record([{"sequence":2,"kind":"input","content":"bad"}]);return {"ok":g["ok"] and not b["ok"],"sequence_counter_proof":not b["ok"]}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("command",choices=("record","verify","probe"));p.add_argument("--input");a=p.parse_args(argv);d=json.load(open(a.input)) if a.input else {};o=probe() if a.command=="probe" else record(d.get("events")) if a.command=="record" else verify(d);print(json.dumps(o,sort_keys=True));return 0 if o["ok"] else 2
