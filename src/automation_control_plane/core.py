TRANS={"pending":{"approved","cancelled"},"approved":{"running","cancelled"},"running":{"paused","completed","failed"},"paused":{"running","cancelled"},"completed":set(),"failed":set(),"cancelled":set()}
def transition(job,target,*,approved_by=None,kill_switch=False):
 state=job["state"]
 if kill_switch and state in {"approved","running","paused"}: return {**job,"state":"cancelled","reason":"kill_switch"}
 if target not in TRANS.get(state,set()): return {**job,"state":"failed","reason":"invalid_transition"}
 if target=="approved" and not approved_by: return {**job,"state":"failed","reason":"approval_missing"}
 if target=="running" and job.get("spent",0)>=job.get("budget",0): return {**job,"state":"failed","reason":"budget_exhausted"}
 result={**job,"state":target}
 if approved_by: result["approved_by"]=approved_by
 return result
def run(data): return transition(**data)

