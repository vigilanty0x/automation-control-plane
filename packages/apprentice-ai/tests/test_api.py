from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest

from apprentice_ai.api import MAX_REQUEST_BYTES, create_server


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.token = "test-token-abcdefghijklmnopqrstuvwxyz"
        self.server = create_server(self.temp.name, token=self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, method: str, path: str, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        payload = None if body is None else json.dumps(body)
        final_headers = dict(headers or {})
        if body is not None:
            final_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=final_headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def bearer(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": f"test-key-{self._testMethodName}",
        }

    def test_health_is_public_but_api_requires_auth(self) -> None:
        status, headers, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertRegex(headers["X-Request-ID"], r"^req_[0-9a-f]{32}$")
        self.assertFalse(json.loads(body)["execution"])
        status, _, body = self.request("GET", "/api/v1/capabilities")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "AUTH_REQUIRED")

    def test_bootstrap_sets_hardened_cookie_and_cookie_posts_need_origin(self) -> None:
        status, headers, _ = self.request("GET", f"/?token={self.server.bootstrap_ticket}")
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(self.token, cookie)
        status, _, _ = self.request("GET", f"/?token={self.server.bootstrap_ticket}")
        self.assertEqual(status, 403)
        cookie_header = {"Cookie": cookie.split(";", 1)[0]}
        status, _, body = self.request("POST", "/api/v1/profiles", body={"name": "Denied"}, headers=cookie_header)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "CSRF_REJECTED")
        cookie_header["Origin"] = self.server.origin
        cookie_header["Idempotency-Key"] = "test-cookie-create-0001"
        status, _, body = self.request("POST", "/api/v1/profiles", body={"name": "Allowed"}, headers=cookie_header)
        self.assertEqual(status, 201, body)

    def test_demo_and_dashboard_are_observable_and_preview_only(self) -> None:
        status, _, body = self.request("POST", "/api/v1/demo", body={}, headers=self.bearer())
        self.assertEqual(status, 201, body)
        result = json.loads(body)
        self.assertEqual(result["status"], "success_proved")
        self.assertFalse(result["preview"]["execution_allowed"])
        self.assertEqual(result["routine"]["status"], "compilable")
        self.assertEqual(result["question"]["status"], "answered")
        status, _, page = self.request("GET", "/", headers=self.bearer())
        self.assertEqual(status, 200)
        self.assertIn(b"aper\xc3\xa7u uniquement", page)
        status, _, resources = self.request(
            "GET", f"/api/v1/profiles/{result['profile_id']}/skills", headers=self.bearer()
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(resources)["skills"]), 1)
        episode_id = result["episodes"]["ids"][0]
        status, _, _ = self.request(
            "GET",
            f"/api/v1/profiles/{result['profile_id']}/episodes/{episode_id}/unexpected",
            headers=self.bearer(),
        )
        self.assertEqual(status, 404)

    def test_idempotency_replays_identical_create_and_conflicts_on_body_change(self) -> None:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "stable-create-key-0001",
        }
        first = self.request("POST", "/api/v1/profiles", body={"name": "One"}, headers=headers)
        second = self.request("POST", "/api/v1/profiles", body={"name": "One"}, headers=headers)
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 201)
        self.assertEqual(first[2], second[2])
        conflict = self.request("POST", "/api/v1/profiles", body={"name": "Two"}, headers=headers)
        self.assertEqual(conflict[0], 409)
        self.assertEqual(json.loads(conflict[2])["error"]["code"], "IDEMPOTENCY_CONFLICT")
        status, _, profiles = self.request("GET", "/api/v1/profiles", headers=self.bearer())
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(profiles)["profiles"]), 1)

    def test_mutation_requires_key_and_localhost_host_is_rejected(self) -> None:
        status, _, body = self.request(
            "POST",
            "/api/v1/profiles",
            body={"name": "Missing key"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "IDEMPOTENCY_KEY_REQUIRED")
        status, _, body = self.request(
            "GET",
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {self.token}", "Host": f"localhost:{self.port}"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "HOST_REJECTED")

    def test_invalid_answer_types_are_a_stable_client_error(self) -> None:
        demo_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "invalid-answer-demo-0001",
        }
        status, _, body = self.request("POST", "/api/v1/demo", body={}, headers=demo_headers)
        self.assertEqual(status, 201)
        demo = json.loads(body)
        invalid_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "invalid-answer-body-0001",
        }
        status, _, body = self.request(
            "POST",
            f"/api/v1/profiles/{demo['profile_id']}/questions/{demo['question']['id']}/answer",
            body={"choice": []},
            headers=invalid_headers,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "VALIDATION_ERROR")
        replay = self.request(
            "POST",
            f"/api/v1/profiles/{demo['profile_id']}/questions/{demo['question']['id']}/answer",
            body={"choice": []},
            headers=invalid_headers,
        )
        self.assertEqual(replay[0], 400)
        self.assertEqual(replay[2], body)
        conflict = self.request(
            "POST",
            f"/api/v1/profiles/{demo['profile_id']}/questions/{demo['question']['id']}/answer",
            body={"choice": "yes"},
            headers=invalid_headers,
        )
        self.assertEqual(conflict[0], 409)
        self.assertEqual(json.loads(conflict[2])["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_purge_route_removes_profile_scoped_idempotency_payloads(self) -> None:
        demo_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "purge-scope-demo-0001",
        }
        status, _, body = self.request("POST", "/api/v1/demo", body={}, headers=demo_headers)
        self.assertEqual(status, 201)
        demo = json.loads(body)
        routine_id = demo["routine"]["routine_id"].encode()
        purge_headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "purge-scope-action-0001",
        }
        status, _, purge = self.request(
            "POST",
            f"/api/v1/profiles/{demo['profile_id']}/purge",
            body={"confirmation": demo["profile_id"]},
            headers=purge_headers,
        )
        self.assertEqual(status, 200, purge)
        from pathlib import Path

        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(routine_id, raw)

    def test_authentication_tokens_cannot_be_persisted_as_idempotency_keys(self) -> None:
        status, _, body = self.request(
            "POST",
            "/api/v1/profiles",
            body={"name": "Secret key attempt"},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": self.token,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "IDEMPOTENCY_SECRET_REJECTED")
        from pathlib import Path

        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(self.token.encode(), raw)

    def test_preview_response_is_sanitized_before_first_send_and_replay(self) -> None:
        status, _, body = self.request(
            "POST",
            "/api/v1/demo",
            body={},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "preview-secret-demo-0001",
            },
        )
        self.assertEqual(status, 201)
        demo = json.loads(body)
        skill = demo["skill"]
        secret = "private-preview-password-12345"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": "preview-secret-request-0001",
        }
        path = (
            f"/api/v1/profiles/{demo['profile_id']}/skills/"
            f"{skill['skill_id']}/{skill['version']}/preview"
        )
        first = self.request("POST", path, body={"inputs": {"password": secret}}, headers=headers)
        second = self.request("POST", path, body={"inputs": {"password": secret}}, headers=headers)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[2], second[2])
        self.assertNotIn(secret.encode(), first[2])
        self.assertEqual(json.loads(first[2])["inputs"]["password"], "[REDACTED:SENSITIVE_KEY]")
        from pathlib import Path

        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(secret.encode(), raw)

    def test_too_deep_body_is_rejected_before_idempotency_reservation(self) -> None:
        nested = {}
        for _ in range(34):
            nested = {"value": nested}
        key = "too-deep-pre-domain-0001"
        headers = {"Authorization": f"Bearer {self.token}", "Idempotency-Key": key}
        first = self.request("POST", "/api/v1/profiles", body=nested, headers=headers)
        second = self.request("POST", "/api/v1/profiles", body=nested, headers=headers)
        self.assertEqual(first[0], 400)
        self.assertEqual(first[2], second[2])
        self.assertEqual(json.loads(first[2])["error"]["code"], "BODY_INVALID")
        from apprentice_ai.service import database_path
        from apprentice_ai.store import EventStore

        with EventStore(database_path(self.temp.name)) as store:
            count = store.connection.execute(
                "SELECT COUNT(*) FROM idempotency WHERE idempotency_key=?", (key,)
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_guided_observation_exposes_manual_question_and_verified_chains(self) -> None:
        status, _, body = self.request(
            "POST",
            "/api/v1/demo/observe",
            body={},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": "guided-observation-0001",
            },
        )
        self.assertEqual(status, 201)
        result = json.loads(body)
        self.assertEqual(result["status"], "awaiting_human_answer")
        self.assertEqual(result["question"]["status"], "queued")
        session = result["seed"]["sessions"][0]
        status, _, chain = self.request(
            "GET",
            f"/api/v1/profiles/{result['profile_id']}/sessions/{session}/verify",
            headers=self.bearer(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(chain)["sealed"])

    def test_oversized_body_and_host_rebinding_are_rejected(self) -> None:
        status, _, body = self.request(
            "POST",
            "/api/v1/profiles",
            body={"name": "Host"},
            headers={**self.bearer(), "Host": "evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "HOST_REJECTED")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        connection.putrequest("POST", "/api/v1/profiles")
        connection.putheader("Authorization", f"Bearer {self.token}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        payload = response.read()
        self.assertEqual(response.status, 413)
        self.assertEqual(response.getheader("Connection"), "close")
        self.assertEqual(json.loads(payload)["error"]["code"], "BODY_TOO_LARGE")
        connection.close()

        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as stream:
            request = (
                f"POST /api/v1/profiles HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                f"Authorization: Bearer {self.token}\r\nIdempotency-Key: socket-close-test-0001\r\n"
                f"Content-Type: application/json\r\nContent-Length: {MAX_REQUEST_BYTES + 1}\r\n\r\n"
                "{}GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            ).encode()
            stream.sendall(request)
            received = b""
            while True:
                chunk = stream.recv(65536)
                if not chunk:
                    break
                received += chunk
        self.assertEqual(received.count(b"HTTP/1.1"), 1)
        self.assertNotIn(b'"status":"ok"', received)


if __name__ == "__main__":
    unittest.main()
