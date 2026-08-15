import unittest
from agent_retry_kit.core import *
class T(unittest.TestCase):
 def test_retry(self): self.assertEqual(decide(0,"timeout").delay_ms,100)
 def test_backoff(self): self.assertEqual(decide(2,"rate_limit",base_ms=10).delay_ms,40)
 def test_cap(self): self.assertEqual(decide(10,"transient",max_attempts=20,cap_ms=50).delay_ms,50)
 def test_exhausted(self): self.assertFalse(decide(3,"timeout").retry)
 def test_permanent(self): self.assertEqual(decide(0,"auth").reason,"non_retryable")
if __name__=="__main__": unittest.main()

