"""HTTP API your sniper queries in its hot path (stdlib only, sub-millisecond lookups).

GET  /v1/health
GET  /v1/deployer/{address}            cached verdict (+ list overrides, family fallback)
GET  /v1/deployer/{address}?refresh=1  backfill + full re-analysis (slow; seconds to minutes)
GET  /v1/mint/{mint}                   the launch and its deployers' verdicts
GET  /v1/copy/{wallet}                 cached copytrade verdict  (?refresh=1 to recompute)
GET  /v1/rules                         everything in one document (same as `snipetech export`)
GET  /v1/signals?since={id}            live signals emitted by `snipetech watch`
GET  /v1/lists
POST /v1/lists/{list}/{address}        body: {"reason": "..."}
DELETE /v1/lists/{list}/{address}

Set `api.api_key` (or SNIPETECH_API_KEY) to require an `X-API-Key` header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .service import Service

log = logging.getLogger(__name__)


class _Loop:
    """One background event loop for the slow (RPC-backed) endpoints."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro, timeout: float = 600):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


def make_handler(svc: Service, api_key: str, loop: _Loop):
    class Handler(BaseHTTPRequestHandler):
        server_version = "snipetech/0.1"

        def log_message(self, fmt, *args):  # quiet by default
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, data) -> None:
            body = json.dumps(data, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self) -> bool:
            if api_key and self.headers.get("X-API-Key") != api_key:
                self._send(401, {"error": "missing or invalid X-API-Key"})
                return False
            return True

        def _route(self, method: str) -> None:
            if not self._auth():
                return
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            refresh = q.get("refresh") in ("1", "true", "yes")
            try:
                if parts[:1] != ["v1"]:
                    return self._send(404, {"error": "not found"})
                p = parts[1:]
                if method == "GET" and p == ["health"]:
                    return self._send(200, {"ok": True, **svc.status()})
                if method == "GET" and len(p) == 2 and p[0] == "deployer":
                    if refresh:
                        rep = loop.run(svc.analyze_deployer(p[1]))
                        return self._send(200, svc.deployer_verdict(p[1]) | {"report": rep} if q.get("full") else svc.deployer_verdict(p[1]))
                    return self._send(200, svc.deployer_verdict(p[1]))
                if method == "GET" and len(p) == 2 and p[0] == "mint":
                    return self._send(200, loop.run(svc.check_mint(p[1], refresh=refresh), timeout=120))
                if method == "GET" and len(p) == 2 and p[0] == "copy":
                    if refresh:
                        loop.run(svc.analyze_copy(p[1]))
                    return self._send(200, svc.store.get_score("copy", p[1]) or {"address": p[1], "verdict": "UNKNOWN"})
                if method == "GET" and p == ["rules"]:
                    return self._send(200, svc.export_rules())
                if method == "GET" and p == ["signals"]:
                    return self._send(200, svc.store.signals_since(int(q.get("since", 0)), int(q.get("limit", 200))))
                if method == "GET" and p == ["lists"]:
                    return self._send(200, svc.lists())
                if len(p) == 3 and p[0] == "lists" and method in ("POST", "DELETE"):
                    if method == "DELETE":
                        return self._send(200, svc.list_remove(p[2], p[1]))
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                    return self._send(200, svc.list_add(p[2], p[1], body.get("reason", "")))
                return self._send(404, {"error": "not found"})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:  # keep the server alive for the sniper
                log.exception("request failed")
                return self._send(500, {"error": str(exc)})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

    return Handler


def make_server(cfg: Config, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    svc = Service(cfg)
    handler = make_handler(svc, cfg.api.api_key, _Loop())
    return ThreadingHTTPServer((host or cfg.api.host, cfg.api.port if port is None else port), handler)


def serve(cfg: Config, host: str | None = None, port: int | None = None) -> None:
    server = make_server(cfg, host, port)
    h, p = server.server_address[:2]
    print(f"snipetech API on http://{h}:{p}/v1  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
