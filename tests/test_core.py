import unittest
from automation_control_plane.core import transition
class T(unittest.TestCase):
 def job(self,state="pending"): return {"id":"j","state":state,"spent":0,"budget":1}
 def test_approve(self): self.assertEqual(transition(self.job(),"approved",approved_by="owner")["state"],"approved")
 def test_missing_approval(self): self.assertEqual(transition(self.job(),"approved")["state"],"failed")
 def test_run(self): self.assertEqual(transition(self.job("approved"),"running")["state"],"running")
 def test_budget(self): x=self.job("approved"); x["spent"]=1; self.assertEqual(transition(x,"running")["reason"],"budget_exhausted")
 def test_kill(self): self.assertEqual(transition(self.job("running"),"paused",kill_switch=True)["state"],"cancelled")
if __name__=="__main__": unittest.main()

