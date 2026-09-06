from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from automation_control_plane.agentops.cli import main


class AdapterCliTests(unittest.TestCase):
    def test_adapter_rehearsal_cli_emits_reversible_receipt(self) -> None:
        payload = {
            "source_repository": "context-window-budgeter",
            "source_sha": "35bb3e05d05ad870715b740143c429f08eda25e7",
            "source_payload": {
                "window_tokens": 8,
                "output_reserve": 2,
                "sections": [
                    {"name": "a", "tokens": 4, "required": False, "priority": 10},
                    {"name": "b", "tokens": 2, "required": False, "priority": 10},
                    {"name": "system", "tokens": 2, "required": True},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adapter.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            first = io.StringIO()
            second = io.StringIO()
            with redirect_stdout(first):
                first_code = main(["adapter-rehearsal", "--input", str(path)])
            with redirect_stdout(second):
                second_code = main(["adapter-rehearsal", "--input", str(path)])
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(first.getvalue(), second.getvalue())
        result = json.loads(first.getvalue())
        self.assertEqual(result["kind"], "adapter_rehearsal")
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["details"]["rehearsal_only"])
        self.assertFalse(result["details"]["alias_activated"])
        self.assertFalse(result["details"]["migration_performed"])


if __name__ == "__main__":
    unittest.main()
