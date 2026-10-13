"""JARVIS tool server (Model Context Protocol over HTTP, localhost only).

Lets an external agent - Claude Code - use JARVIS's hands: screen reading, clicking,
typing, apps, files, email, messaging, media... Every call runs inside the main JARVIS
process, so the normal permission policy, approval prompts, audit log and UI events all
apply. Bound to 127.0.0.1 with a random per-run bearer token.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from jarvis.core.cancel import CancelToken

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
EXCLUDED = {"delegate_to_coding_agent"}  # avoid recursion


class ToolServer:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.token = secrets.token_urlsafe(24)
        self.task_id: str | None = None
        self.cancel: CancelToken | None = None
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"

    def start(self) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence default stderr logging
                pass

            def _send(self, code: int, payload: Any | None = None) -> None:
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(code)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - no server-initiated stream
                self._send(405, {"error": "use POST"})

            def do_DELETE(self) -> None:  # noqa: N802
                self._send(200, {})

            def do_POST(self) -> None:  # noqa: N802
                if self.headers.get("Authorization", "") != f"Bearer {server.token}":
                    self._send(401, {"error": "unauthorized"})
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                try:
                    msg = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, {"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}})
                    return
                if isinstance(msg, list):
                    replies = [r for r in (server.handle(m) for m in msg) if r is not None]
                    self._send(200, replies) if replies else self._send(202)
                    return
                reply = server.handle(msg)
                self._send(200, reply) if reply is not None else self._send(202)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, name="jarvis-mcp", daemon=True).start()
        log.info("JARVIS tool server listening on %s", self.url)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None

    # ------------------------------------------------------------- JSON-RPC
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method", "")
        mid = msg.get("id")
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "jarvis", "version": "4"},
                    "instructions": "These tools act on the user's real Windows computer. External/destructive "
                                    "actions ask the user for approval automatically.",
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.parameters}
                    for t in self.app.tools.tools.values() if t.name not in EXCLUDED]}
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = self.call(params.get("name", ""), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        except Exception as exc:  # noqa: BLE001 - always answer the client
            log.exception("tool server error")
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in EXCLUDED or name not in self.app.tools.tools:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        res = self.app.tools.execute(name, args, cancel=self.cancel or CancelToken(), task_id=self.task_id)
        content: list[dict[str, Any]] = [{"type": "text", "text": res.for_model()}]
        for img in res.images[:2]:
            content.append({"type": "image", "data": img, "mimeType": "image/png"})
        return {"content": content, "isError": not res.ok}
