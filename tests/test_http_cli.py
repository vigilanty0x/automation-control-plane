from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from automation_control_plane.engine import ControlPlane
from automation_control_plane.http_api import make_handler
from automation_control_plane.storage import ControlPlaneStore
from tests.support import workflow


class HttpAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "control.db"
        self.store = ControlPlaneStore(self.database); self.store.initialize()
        self.control = ControlPlane(self.store); self.control.register_workflow(workflow(), principal="admin")

    def run_cli(self, *arguments, input_value=None):
        environment = dict(os.environ); environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        return subprocess.run([sys.executable, "-m", "automation_control_plane.cli", *map(str, arguments)],
                              input=input_value, text=True, capture_output=True, check=False, env=environment)

    def test_cli_init_register_submit_show_worker_audit_and_backup(self):
        database = Path(self.temporary.name) / "cli.db"; definition = Path(self.temporary.name) / "flow.json"
        definition.write_text(json.dumps(workflow()), encoding="utf-8")
        self.assertEqual(self.run_cli("init", "--db", database).returncode, 0)
        self.assertEqual(self.run_cli("role", "--db", database, "cli-worker", "worker").returncode, 0)
        self.assertEqual(self.run_cli("register", "--db", database, definition).returncode, 0)
        submitted = self.run_cli("submit", "--db", database, "test-flow", "--idempotency-key", "cli-key")
        self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        job_id = json.loads(submitted.stdout)["result"]["job_id"]
        worker = self.run_cli("worker", "--db", database, "--principal", "cli-worker")
        self.assertEqual(json.loads(worker.stdout)["result"]["results"][0]["status"], "succeeded")
        shown = self.run_cli("show", "--db", database, job_id)
        self.assertEqual(json.loads(shown.stdout)["result"]["state"], "completed")
        audit = self.run_cli("audit", "--db", database)
        self.assertTrue(json.loads(audit.stdout)["result"]["valid"])
        backup = Path(self.temporary.name) / "cli-backup.db"
        self.assertEqual(self.run_cli("backup", "--db", database, backup).returncode, 0)
        self.assertTrue(backup.is_file())

    def test_legacy_simulation_contract_remains_available(self):
        payload = {"job": {"id": "j", "version": 1, "action": "a", "state": "pending", "spent": 0, "budget": 1}, "target": "approved"}
        completed = self.run_cli(input_value=json.dumps(payload))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["result"]["status"], "simulation_only")

    def test_cli_errors_are_bounded_json(self):
        completed = self.run_cli("show", "--db", self.database, "missing")
        body = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2); self.assertFalse(body["success"]); self.assertLessEqual(len(body["message"]), 512)
        syntax = self.run_cli("submit", "--db", self.database, "test-flow")
        syntax_body = json.loads(syntax.stdout)
        self.assertEqual(syntax.returncode, 2); self.assertIn("invalid command arguments", syntax_body["message"])

    def test_http_api_is_read_only_and_sets_security_headers(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.control))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(base + "/api/workflows", timeout=3) as response:
            body = json.loads(response.read()); self.assertEqual(len(body["items"]), 1)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(base + "/api/jobs", data=b"{}", method="POST"), timeout=3)
        self.assertEqual(raised.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
