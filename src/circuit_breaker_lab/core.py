from dataclasses import dataclass
@dataclass
class Circuit:
 threshold:int=3; cooldown_ms:int=1000; failures:int=0; opened_at:int|None=None; state:str="closed"
 def allow(self,now_ms):
  if self.state=="open" and now_ms-(self.opened_at or 0)>=self.cooldown_ms: self.state="half_open"; return True
  return self.state!="open"
 def record(self,success,now_ms):
  if success: self.failures=0; self.opened_at=None; self.state="closed"; return
  self.failures+=1
  if self.state=="half_open" or self.failures>=self.threshold: self.state="open"; self.opened_at=now_ms
def simulate(events,threshold=3,cooldown_ms=1000):
 c=Circuit(threshold,cooldown_ms); out=[]
 for e in events:
  allowed=c.allow(e["at_ms"])
  if allowed: c.record(e["success"],e["at_ms"])
  out.append({"allowed":allowed,"state":c.state})
 return {"events":out,"state":c.state}
def run(data): return simulate(**data)

