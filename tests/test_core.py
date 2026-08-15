import unittest

from handoff_markdown_cli import build, probe

SPEC = {"title": "t", "summary": "s", "completed": ["c"], "pending": ["p"],
        "evidence": ["e"], "risks": ["r"], "next_owner": "o"}


class Tests(unittest.TestCase):
    def test_evidence_and_owner(self):
        self.assertTrue(build(SPEC)["ok"])
        self.assertFalse(build({**SPEC, "evidence": []})["ok"])
        self.assertFalse(build({**SPEC, "next_owner": ""})["ok"])

    def test_safe_bounded_markdown(self):
        result = build({**SPEC, "summary": "<b># fake</b>"})
        self.assertNotIn("<b>", result["markdown"])
        self.assertIn("\\#", result["markdown"])
        self.assertFalse(build({**SPEC, "risks": ["one\ntwo"]})["ok"])
        self.assertFalse(build({**SPEC, "pending": ["x"] * 101})["ok"])

    def test_malformed_does_not_crash(self):
        for value in (None, [], {**SPEC, "completed": [1]}, {**SPEC, "extra": 1}):
            self.assertFalse(build(value)["ok"])

    def test_probe(self):
        self.assertTrue(probe()["ok"])


if __name__ == "__main__":
    unittest.main()
