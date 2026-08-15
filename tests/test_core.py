import unittest
from circuit_breaker_lab.core import simulate
class T(unittest.TestCase):
 def test_closed(self): self.assertEqual(simulate([{"at_ms":0,"success":True}])["state"],"closed")
 def test_open(self): self.assertEqual(simulate([{"at_ms":0,"success":False}],threshold=1)["state"],"open")
 def test_reject(self): self.assertFalse(simulate([{"at_ms":0,"success":False},{"at_ms":1,"success":True}],threshold=1)["events"][1]["allowed"])
 def test_half_success(self): self.assertEqual(simulate([{"at_ms":0,"success":False},{"at_ms":1000,"success":True}],threshold=1)["state"],"closed")
 def test_half_failure(self): self.assertEqual(simulate([{"at_ms":0,"success":False},{"at_ms":1000,"success":False}],threshold=1)["state"],"open")
if __name__=="__main__": unittest.main()

