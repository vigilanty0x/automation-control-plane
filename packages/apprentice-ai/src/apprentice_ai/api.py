"""Dependency-free loopback HTTP API and authenticated local dashboard."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import threading
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .benchmark import run_benchmarks
from .contracts import require_id
from .errors import ApprenticeError, IntegrityError, NotFoundError, PolicyError, ValidationError
from .learning import apply_answer, discover_routine, generate_question, segment_sessions
from .service import (
    capabilities,
    database_path,
    ensure_data_dir,
    prepare_reference_observation,
    run_reference_demo,
)
from .skills import compile_skill, preview_stored_skill
from .store import EventStore
from .strictjson import canonical_bytes, loads_bytes

MAX_REQUEST_BYTES = 1024 * 1024
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,256}$")


class ApprenticeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data_dir: Path, token: str, address: tuple[str, int]) -> None:
        self.data_dir = data_dir
        self.auth_token = token
        self.session_token = secrets.token_urlsafe(32)
        self.bootstrap_ticket = secrets.token_urlsafe(32)
        self.bootstrap_consumed = False
        self.bootstrap_lock = threading.Lock()
        self.idempotency_lock = threading.RLock()
        super().__init__(address, ApprenticeHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class ApprenticeHandler(BaseHTTPRequestHandler):
    server: ApprenticeHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(3.0)

    def handle_one_request(self) -> None:
        self._cached_request_id = None
        super().handle_one_request()

    def log_message(self, _format: str, *args: object) -> None:
        # Deliberately suppress request-target logging: bootstrap URLs contain a one-time token.
        return

    def _headers(self, content_type: str, length: int, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Request-ID", self._request_id())
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, value: Any, *, status: int = 200) -> None:
        payload = canonical_bytes(value)
        try:
            self._headers("application/json; charset=utf-8", len(payload), status=status)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def _request_id(self) -> str:
        cached = getattr(self, "_cached_request_id", None)
        if cached is not None:
            return cached
        supplied = self.headers.get("X-Request-ID", "")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}", supplied):
            cached = supplied
        else:
            cached = f"req_{secrets.token_hex(16)}"
        self._cached_request_id = cached
        return cached

    def _error(self, exc: ApprenticeError, *, status: int | None = None) -> None:
        if status is None:
            status = self._status_for_error(exc)
        self._json({"error": {"code": exc.code, "message": exc.message}}, status=int(status))

    @staticmethod
    def _status_for_error(exc: ApprenticeError) -> int:
        if isinstance(exc, NotFoundError):
            return HTTPStatus.NOT_FOUND
        if isinstance(exc, PolicyError):
            return HTTPStatus.FORBIDDEN
        if isinstance(exc, IntegrityError):
            return HTTPStatus.CONFLICT
        return HTTPStatus.BAD_REQUEST

    def _host_valid(self) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_address[1]}"}
        return host in allowed

    def _bearer_authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return secrets.compare_digest(header[7:], self.server.auth_token)

    def _cookie_authenticated(self) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        morsel = cookie.get("apprentice_session")
        return bool(morsel and secrets.compare_digest(morsel.value, self.server.session_token))

    def _authenticated(self) -> bool:
        return self._bearer_authenticated() or self._cookie_authenticated()

    def _require_auth(self) -> None:
        if not self._authenticated():
            raise PolicyError("authentication required", code="AUTH_REQUIRED")

    def _require_mutation_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            raise PolicyError("request origin is not allowed", code="ORIGIN_REJECTED")
        if self._cookie_authenticated() and not self._bearer_authenticated() and origin != self.server.origin:
            raise PolicyError("cookie mutation requires same-origin proof", code="CSRF_REJECTED")

    def _request_object(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ValidationError("chunked request bodies are not accepted", code="BODY_INVALID")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise ValidationError("Content-Type must be application/json", code="BODY_INVALID")
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "")
        except ValueError as exc:
            raise ValidationError("valid Content-Length is required", code="BODY_INVALID") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValidationError("request body exceeds 1 MiB", code="BODY_TOO_LARGE")
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise ValidationError("request body ended early", code="BODY_INVALID")
        try:
            value = loads_bytes(
                payload, max_bytes=MAX_REQUEST_BYTES, max_depth=30, max_nodes=100_000
            )
        except ValidationError as exc:
            raise ValidationError(exc.message, code="BODY_INVALID") from exc
        if not isinstance(value, dict):
            raise ValidationError("request JSON must be an object", code="BODY_INVALID")
        return value

    @staticmethod
    def _segments(path: str) -> list[str]:
        if "\\" in path or "%2f" in path.casefold() or "%5c" in path.casefold():
            raise ValidationError("encoded path separators are forbidden")
        return [unquote(item) for item in path.split("/") if item]

    def _bootstrap(self, query: str) -> bool:
        try:
            params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
        except ValueError:
            return False
        supplied = params.get("token", [])
        with self.server.bootstrap_lock:
            if (
                len(supplied) != 1
                or self.server.bootstrap_consumed
                or not secrets.compare_digest(supplied[0], self.server.bootstrap_ticket)
            ):
                return False
            self.server.bootstrap_consumed = True
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"apprentice_session={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Request-ID", self._request_id())
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _static(self, name: str, content_type: str) -> None:
        payload = resources.files("apprentice_ai").joinpath("web", name).read_bytes()
        self._headers(content_type, len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        try:
            if not self._host_valid():
                raise PolicyError("Host header is not loopback", code="HOST_REJECTED")
            target = urlsplit(self.path)
            if target.path == "/health":
                self._json({"status": "ok", "bind": "loopback", "execution": False})
                return
            if target.path == "/" and target.query and self._bootstrap(target.query):
                return
            self._require_auth()
            if target.path == "/":
                self._static("index.html", "text/html; charset=utf-8")
                return
            if target.path == "/app.css":
                self._static("app.css", "text/css; charset=utf-8")
                return
            if target.path == "/components.css":
                self._static("components.css", "text/css; charset=utf-8")
                return
            if target.path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
                return
            if not target.path.startswith("/api/v1/"):
                raise NotFoundError("route not found")
            result = self._get_api(target.path, target.query)
            self._json(result)
        except ApprenticeError as exc:
            self._error(exc)
        except Exception:
            self._error(
                ApprenticeError("INTERNAL_ERROR", "unexpected internal failure", 70),
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _get_api(self, path: str, query: str) -> Any:
        parts = self._segments(path)
        if parts == ["api", "v1", "capabilities"]:
            return capabilities()
        if parts == ["api", "v1", "profiles"]:
            with EventStore(database_path(self.server.data_dir)) as store:
                return {"profiles": store.list_profiles()}
        if len(parts) < 5 or parts[:3] != ["api", "v1", "profiles"]:
            raise NotFoundError("route not found")
        profile_id = require_id(parts[3], prefix="pro")
        resource = parts[4]
        with EventStore(database_path(self.server.data_dir)) as store:
            if resource == "sessions" and len(parts) == 5:
                return {"sessions": store.list_sessions(profile_id)}
            if resource == "sessions" and len(parts) == 7 and parts[6] == "verify":
                return store.verify_chain(profile_id, require_id(parts[5], prefix="ses"))
            if resource == "timeline" and len(parts) == 5:
                try:
                    params = parse_qs(query, max_num_fields=8)
                    limit = int(params.get("limit", ["1000"])[0])
                    offset = int(params.get("offset", ["0"])[0])
                    session = params.get("session_id", [None])[0]
                except (ValueError, IndexError) as exc:
                    raise ValidationError("invalid timeline query") from exc
                return {
                    "events": store.list_events(
                        profile_id, session_id=session, limit=limit, offset=offset
                    )
                }
            if resource == "episodes":
                if len(parts) == 5:
                    return {"episodes": store.list_episodes(profile_id)}
                if len(parts) == 6:
                    return store.get_episode(profile_id, require_id(parts[5], prefix="epi"))
                raise NotFoundError("route not found")
            if resource == "routines":
                if len(parts) == 5:
                    return {"routines": store.list_routines(profile_id)}
                if len(parts) == 6:
                    return store.get_routine(profile_id, require_id(parts[5], prefix="rou"))
                raise NotFoundError("route not found")
            if resource == "questions" and len(parts) == 5:
                return {"questions": store.list_questions(profile_id)}
            if resource == "memories":
                if len(parts) == 5:
                    return {"memories": store.list_memories(profile_id)}
                if len(parts) == 6:
                    return store.get_memory(profile_id, require_id(parts[5], prefix="mem"))
                raise NotFoundError("route not found")
            if resource == "skills" and len(parts) == 5:
                return {"skills": store.list_skills(profile_id)}
            if resource == "imports":
                if len(parts) == 5:
                    return {"imports": store.list_imports(profile_id)}
                if len(parts) == 6:
                    return store.get_import(profile_id, require_id(parts[5], prefix="imp"))
                raise NotFoundError("route not found")
            if resource == "audit" and len(parts) == 5:
                return {"audit": store.audit_events(profile_id)}
        raise NotFoundError("route not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        try:
            if not self._host_valid():
                raise PolicyError("Host header is not loopback", code="HOST_REJECTED")
            self._require_auth()
            self._require_mutation_origin()
            target = urlsplit(self.path)
            body = self._request_object()
            result, status = self._idempotent_post(target.path, body)
            self._json(result, status=status)
        except ApprenticeError as exc:
            self.close_connection = True
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if exc.code == "BODY_TOO_LARGE" else None
            self._error(exc, status=status)
        except TimeoutError:
            self.close_connection = True
            self._error(
                ValidationError("request body read timed out", code="BODY_TIMEOUT"),
                status=HTTPStatus.REQUEST_TIMEOUT,
            )
        except Exception:
            self.close_connection = True
            self._error(
                ApprenticeError("INTERNAL_ERROR", "unexpected internal failure", 70),
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _idempotent_post(self, path: str, body: dict[str, Any]) -> tuple[Any, int]:
        key = self.headers.get("Idempotency-Key")
        if key is None:
            raise ValidationError(
                "Idempotency-Key is required for mutations", code="IDEMPOTENCY_KEY_REQUIRED"
            )
        if any(
            secrets.compare_digest(key, secret)
            for secret in (
                self.server.auth_token,
                self.server.session_token,
                self.server.bootstrap_ticket,
            )
        ):
            raise ValidationError(
                "authentication material cannot be used as Idempotency-Key",
                code="IDEMPOTENCY_SECRET_REJECTED",
            )
        digest = f"sha256:{hashlib.sha256(canonical_bytes(body)).hexdigest()}"
        with self.server.idempotency_lock:
            with EventStore(database_path(self.server.data_dir)) as store:
                replay = store.reserve_idempotency(key, path, digest)
            if replay is not None:
                return replay["response"], int(replay["status"])
            try:
                result, status = self._post_api(path, body)
            except ApprenticeError as exc:
                result = {"error": {"code": exc.code, "message": exc.message}}
                status = self._status_for_error(exc)
            profile_ids = self._profile_scope(path, result)
            with EventStore(database_path(self.server.data_dir)) as store:
                result = store.complete_idempotency(
                    key, path, digest, int(status), result, profile_ids
                )
            return result, int(status)

    @staticmethod
    def _profile_scope(path: str, response: Any) -> list[str]:
        found = set(re.findall(r"pro_[A-Za-z0-9][A-Za-z0-9_.-]{2,95}", path))
        stack = [response]
        nodes = 0
        while stack:
            current = stack.pop()
            nodes += 1
            if nodes > 200_000:
                raise ValidationError("idempotency response scope exceeds node limit")
            if isinstance(current, dict):
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, str) and re.fullmatch(
                r"pro_[A-Za-z0-9][A-Za-z0-9_.-]{2,95}", current
            ):
                found.add(current)
        return sorted(found)

    def _post_api(self, path: str, body: dict[str, Any]) -> tuple[Any, int]:
        parts = self._segments(path)
        if parts == ["api", "v1", "demo"]:
            if body:
                raise ValidationError("demo request takes an empty JSON object")
            return run_reference_demo(self.server.data_dir), HTTPStatus.CREATED
        if parts == ["api", "v1", "demo", "observe"]:
            if body:
                raise ValidationError("observation request takes an empty JSON object")
            return prepare_reference_observation(self.server.data_dir), HTTPStatus.CREATED
        if parts == ["api", "v1", "profiles"]:
            if set(body) != {"name"}:
                raise ValidationError("profile request requires only name")
            with EventStore(database_path(self.server.data_dir)) as store:
                return {"profile_id": store.create_profile(body["name"])}, HTTPStatus.CREATED
        if (
            len(parts) == 5
            and parts[:3] == ["api", "v1", "profiles"]
            and parts[4] == "purge"
        ):
            profile_id = require_id(parts[3], prefix="pro")
            if set(body) != {"confirmation"}:
                raise ValidationError("purge requires exact confirmation")
            with EventStore(database_path(self.server.data_dir)) as store:
                return (
                    store.purge_profile_data(profile_id, confirmation=body["confirmation"]),
                    HTTPStatus.OK,
                )
        if len(parts) < 6 or parts[:3] != ["api", "v1", "profiles"]:
            raise NotFoundError("route not found")
        profile_id = require_id(parts[3], prefix="pro")
        with EventStore(database_path(self.server.data_dir)) as store:
            if parts[4:] == ["episodes", "build"]:
                if body:
                    raise ValidationError("episode build request takes no fields")
                return {"episodes": segment_sessions(store, profile_id)}, HTTPStatus.OK
            if parts[4:] == ["routines", "discover"]:
                if set(body) - {"goal", "effect"}:
                    raise ValidationError("routine discovery fields are goal/effect only")
                if any(
                    value is not None and not isinstance(value, str)
                    for value in (body.get("goal"), body.get("effect"))
                ):
                    raise ValidationError("routine goal/effect must be strings")
                return (
                    discover_routine(
                        store, profile_id, goal=body.get("goal"), effect=body.get("effect")
                    ),
                    HTTPStatus.CREATED,
                )
            if parts[4] == "routines" and len(parts) == 7 and parts[6] == "questions":
                routine_id = require_id(parts[5], prefix="rou")
                if set(body) - {"daily_budget"}:
                    raise ValidationError("question request accepts daily_budget only")
                return (
                    generate_question(
                        store,
                        profile_id,
                        routine_id,
                        daily_budget=body.get("daily_budget", 3),
                    ),
                    HTTPStatus.CREATED,
                )
            if parts[4] == "routines" and len(parts) == 7 and parts[6] == "compile":
                if body:
                    raise ValidationError("compile request takes no fields")
                return compile_skill(store, profile_id, require_id(parts[5], prefix="rou")), HTTPStatus.CREATED
            if parts[4] == "questions" and len(parts) == 7:
                question_id = require_id(parts[5], prefix="qst")
                operation = parts[6]
                if operation == "answer":
                    if set(body) - {"choice", "explanation", "synthetic"} or "choice" not in body:
                        raise ValidationError("answer requires choice and optional explanation/synthetic")
                    if not isinstance(body["choice"], str):
                        raise ValidationError("answer choice must be yes, no or unknown")
                    if not isinstance(body.get("explanation", ""), str):
                        raise ValidationError("answer explanation must be text")
                    if type(body.get("synthetic", False)) is not bool:
                        raise ValidationError("answer synthetic flag must be boolean")
                    return (
                        apply_answer(
                            store,
                            profile_id,
                            question_id,
                            body["choice"],
                            explanation=body.get("explanation", ""),
                            synthetic=body.get("synthetic", False),
                        ),
                        HTTPStatus.OK,
                    )
                targets = {
                    "dismiss": "dismissed",
                    "expire": "expired",
                    "resume": "queued",
                    "snooze": "snoozed",
                }
                if operation not in targets:
                    raise NotFoundError("route not found")
                allowed = {"until"} if operation == "snooze" else set()
                if set(body) - allowed:
                    raise ValidationError("question transition has unsupported fields")
                return (
                    store.transition_question(
                        profile_id,
                        question_id,
                        targets[operation],
                        snoozed_until=body.get("until"),
                    ),
                    HTTPStatus.OK,
                )
            if parts[4] == "skills" and len(parts) == 8 and parts[7] == "preview":
                if set(body) - {"inputs"}:
                    raise ValidationError("preview accepts inputs only")
                inputs = body.get("inputs", {})
                if not isinstance(inputs, dict):
                    raise ValidationError("preview inputs must be an object")
                return (
                    preview_stored_skill(store, profile_id, parts[5], parts[6], inputs),
                    HTTPStatus.OK,
                )
            if parts[4:] == ["bench", "run"]:
                if body:
                    raise ValidationError("benchmark request takes no fields")
                return run_benchmarks(store, profile_id), HTTPStatus.OK
        raise NotFoundError("route not found")


def create_server(
    data_dir: str | Path,
    *,
    port: int = 0,
    token: str | None = None,
) -> ApprenticeHTTPServer:
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValidationError("port must be between 0 and 65535")
    auth_token = token or secrets.token_urlsafe(32)
    if not TOKEN_RE.fullmatch(auth_token):
        raise ValidationError("API token must be 24-256 URL-safe characters")
    root = ensure_data_dir(data_dir)
    # Initialize and validate the store before accepting any request.
    with EventStore(database_path(root)):
        pass
    return ApprenticeHTTPServer(root, auth_token, ("127.0.0.1", port))


def serve(data_dir: str | Path, *, port: int = 8765, token: str | None = None) -> None:
    server = create_server(data_dir, port=port, token=token)
    startup = {
        "status": "listening",
        "bind": "127.0.0.1",
        "port": server.server_address[1],
        "bootstrap_url": f"{server.origin}/?token={server.bootstrap_ticket}",
        "execution_supported": False,
    }
    sys.stdout.write(json.dumps(startup, sort_keys=True) + "\n")
    sys.stdout.flush()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
