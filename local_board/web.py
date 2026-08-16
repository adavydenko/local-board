from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlparse

from .db import Board
from .mcp import handle


def make_handler(board: Board):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalBoard/0.1"

        def _json(self, status: int, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _empty(self, status: int) -> None:
            self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()

        def _actor(self):
            header = self.headers.get("Authorization", "")
            return board.authenticate(header[7:]) if header.startswith("Bearer ") else None

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                body = files("local_board").joinpath("static/index.html").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            actor = self._actor()
            if not actor: return self._json(401, {"error": "Bearer token required"})
            try:
                if path == "/api/dashboard": return self._json(200, board.dashboard())
                if path.startswith("/api/issues/"): return self._json(200, board.get_issue(int(path.rsplit("/", 1)[1])))
                return self._json(404, {"error": "not found"})
            except (KeyError, ValueError) as exc: return self._json(404, {"error": str(exc)})

        def do_POST(self):
            actor = self._actor()
            if not actor: return self._json(401, {"error": "Bearer token required"})
            try:
                data = self._body(); path = urlparse(self.path).path
                if path == "/mcp":
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
                    accept = self.headers.get("Accept", "")
                    if content_type != "application/json":
                        return self._json(415, {"error": "MCP requires Content-Type: application/json"})
                    if "application/json" not in accept or "text/event-stream" not in accept:
                        return self._json(406, {"error": "MCP requires Accept: application/json, text/event-stream"})
                    if isinstance(data, list):
                        responses = [response for item in data if (response := handle(board, actor["id"], item)) is not None]
                        return self._empty(202) if not responses else self._json(200, responses)
                    response = handle(board, actor["id"], data)
                    return self._empty(202) if response is None else self._json(200, response)
                if path == "/api/projects": result = board.create_project(actor["id"], **data)
                elif path == "/api/issues": result = board.create_issue(actor["id"], **data)
                elif path.endswith("/transition"):
                    result = board.transition_issue(actor["id"], int(path.split("/")[3]), data["status"])
                elif path.endswith("/comments"):
                    result = board.add_related(actor["id"], int(path.split("/")[3]), "comment", **data)
                else: return self._json(404, {"error": "not found"})
                return self._json(HTTPStatus.CREATED, result)
            except (KeyError, ValueError, TypeError) as exc: return self._json(400, {"error": str(exc)})

        def log_message(self, fmt, *args):
            print(f"[local-board] {fmt % args}")
    return Handler


def serve(board: Board, host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Local Board: http://{host}:{port}")
    ThreadingHTTPServer((host, port), make_handler(board)).serve_forever()
