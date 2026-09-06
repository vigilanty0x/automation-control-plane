from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_inbox import AgentInbox, NoMissionAvailable
from helpers import profile, spec


class ConcurrencyTests(unittest.TestCase):
    def test_two_workers_cannot_claim_same_mission(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inbox.sqlite3"
            setup = AgentInbox(path); setup.register_agent(profile(agent_id="a")); setup.register_agent(profile(agent_id="b")); mission = setup.enqueue(spec())
            def claim(agent):
                try: return AgentInbox(path).claim(agent, lease_seconds=10)["mission_id"]
                except NoMissionAvailable: return None
            with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(claim, ("a", "b")))
            self.assertEqual(results.count(mission["mission_id"]), 1); self.assertEqual(results.count(None), 1)

    def test_concurrent_idempotent_enqueue_creates_one_mission(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inbox.sqlite3"; AgentInbox(path).initialize()
            def enqueue(_): return AgentInbox(path).enqueue(spec())["mission_id"]
            with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(enqueue, range(8)))
            self.assertEqual(len(set(results)), 1); self.assertEqual(len(AgentInbox(path).list()), 1)


if __name__ == "__main__": unittest.main()

