import json
import unittest

from fixtures import handoff_dict
from agent_handoff.models import Handoff
from agent_handoff.render import render_json, render_markdown


class RenderTests(unittest.TestCase):
    def test_json_contains_identity(self):
        handoff = Handoff.from_dict(handoff_dict())
        payload = json.loads(render_json(handoff))
        self.assertEqual(payload["logical_sha256"], handoff.logical_sha256)

    def test_json_is_deterministic(self):
        handoff = Handoff.from_dict(handoff_dict())
        self.assertEqual(render_json(handoff), render_json(handoff))

    def test_markdown_has_sections_and_status(self):
        output = render_markdown(Handoff.from_dict(handoff_dict()))
        self.assertIn("# Agent Handoff", output)
        self.assertIn("## Acceptance criteria", output)
        self.assertIn("State: `done`", output)
        self.assertIn("Logical SHA-256", output)

    def test_markdown_exposes_no_evidence_for_non_done(self):
        raw = handoff_dict(); raw["state"] = "waiting"; raw["evidence"] = []
        output = render_markdown(Handoff.from_dict(raw))
        self.assertIn("No evidence recorded", output)

    def test_markdown_exposes_open_items(self):
        raw = handoff_dict(); raw["state"] = "waiting"; raw["open_items"] = [{"item_id":"dispute","severity":"medium","kind":"disagreement","description":"Different interpretation"}]
        self.assertIn("disagreement", render_markdown(Handoff.from_dict(raw)))

    def test_markdown_escapes_table_pipe(self):
        raw = handoff_dict(); raw["criteria"][0]["description"] = "one | two"
        self.assertIn("one \\| two", render_markdown(Handoff.from_dict(raw)))

