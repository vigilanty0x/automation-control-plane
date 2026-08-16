"""Optional loopback-only, read-only HTTP API and minimal dashboard."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .engine import ControlPlane
from .storage import NotFoundError


MAX_LIMIT = 500
DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automation Control Plane</title><style>
:root{color-scheme:dark;background:#0b1020;color:#e8edf7;font-family:system-ui,sans-serif}body{max-width:1100px;margin:2rem auto;padding:0 1rem}
h1{letter-spacing:-.03em}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}.card{background:#151d31;border:1px solid #2b3958;border-radius:12px;padding:1rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:.55rem;border-bottom:1px solid #2b3958}code{color:#8bd5ff}.ok{color:#78e08f}.bad{color:#ff7b7b}
</style></head><body><h1>Automation Control Plane</h1><p>Local read-only operational view.</p>
<div class="grid"><section class="card"><h2>Audit</h2><div id="audit">Loading…</div></section><section class="card"><h2>Workflows</h2><div id="workflows">Loading…</div></section></div>
<section class="card" style="margin-top:1rem"><h2>Recent jobs</h2><div id="jobs">Loading…</div></section>
<script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function get(p){const r=await fetch(p);if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
function table(rows,cols){return '<table><thead><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(r[c]??'')+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
Promise.all([get('/api/audit'),get('/api/workflows?limit=20'),get('/api/jobs?limit=50')]).then(([a,w,j])=>{
document.querySelector('#audit').innerHTML='<b class="'+(a.valid?'ok':'bad')+'">'+(a.valid?'VALID':'INVALID')+'</b><p>'+a.events+' chained events</p><code>'+esc(a.head_hash.slice(0,16))+'…</code>';
document.querySelector('#workflows').innerHTML=table(w.items,['workflow_id','version','active']);document.querySelector('#jobs').innerHTML=table(j.items,['job_id','workflow_id','state','budget_spent','budget_limit','updated_at']);
}).catch(e=>document.body.insertAdjacentHTML('beforeend','<p class="bad">'+esc(e.message)+'</p>');
</script></body></html>"""


def _integer(query: dict[str, list[str]], name: str, default: int, minimum: int = 0, maximum: int = MAX_LIMIT) -> int:
    raw = query.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is out of bounds")
    return value


def make_handler(control_plane: ControlPlane, *, principal: str = "admin") -> type[BaseHTTPRequestHandler]:
    class ReadOnlyHandler(BaseHTTPRequestHandler):
        server_version = "AutomationControlPlane/1"

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, status: HTTPStatus, value: Any) -> None:
            body = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            try:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=False)
                if parsed.path == "/":
                    body = DASHBOARD.encode("utf-8")
                    self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                    self.wfile.write(body)
                elif parsed.path == "/health": self._json(HTTPStatus.OK, {"status": "ok", "mode": "read_only"})
                elif parsed.path == "/api/audit": self._json(HTTPStatus.OK, control_plane.verify_audit(principal=principal))
                elif parsed.path == "/api/workflows":
                    limit = _integer(query, "limit", 100, 1); self._json(HTTPStatus.OK, {"items": control_plane.list_workflows(principal=principal, limit=limit)})
                elif parsed.path == "/api/jobs":
                    limit = _integer(query, "limit", 100, 1); state = query.get("state", [None])[0]
                    self._json(HTTPStatus.OK, {"items": control_plane.list_jobs(principal=principal, state=state, limit=limit)})
                elif parsed.path.startswith("/api/jobs/"):
                    job_id = unquote(parsed.path.removeprefix("/api/jobs/"))
                    if not job_id or "/" in job_id or len(job_id) > 256: raise ValueError("invalid job id")
                    self._json(HTTPStatus.OK, control_plane.show_job(job_id, principal=principal))
                elif parsed.path == "/api/events":
                    limit = _integer(query, "limit", 100, 1); after = _integer(query, "after", 0, 0, 2**63 - 1)
                    self._json(HTTPStatus.OK, {"items": control_plane.list_events(principal=principal, after=after, limit=limit)})
                elif parsed.path == "/api/kill-switches":
                    limit = _integer(query, "limit", 100, 1)
                    self._json(HTTPStatus.OK, {"items": control_plane.list_kill_switches(principal=principal, limit=limit)})
                else: self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except NotFoundError as exc: self._json(HTTPStatus.NOT_FOUND, {"error": "not_found", "message": str(exc)})
            except (ValueError, PermissionError) as exc: self._json(HTTPStatus.BAD_REQUEST, {"error": type(exc).__name__, "message": str(exc)[:512]})
            except Exception: self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})

        def do_POST(self) -> None:  # noqa: N802 - explicitly read-only
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only"})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ReadOnlyHandler


def serve(control_plane: ControlPlane, *, host: str = "127.0.0.1", port: int = 8787, principal: str = "admin") -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}: raise ValueError("read-only server may bind only to loopback")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535: raise ValueError("port is out of bounds")
    ThreadingHTTPServer((host, port), make_handler(control_plane, principal=principal)).serve_forever()
