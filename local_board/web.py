"""HTTP transport: a small REST surface plus the JSON-RPC /mcp endpoint."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urlparse

from .db import Board
from .errors import describe

# Imported lazily inside _handle_mcp: keeps the REST surface fully functional
# even while local_board.mcp is mid-rewrite or otherwise broken.


MAX_BODY_BYTES = 1_000_000


class _TooLarge(Exception):
    """Raised internally when a request body exceeds MAX_BODY_BYTES."""


def make_handler(board: Board):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalBoard/0.1"
        protocol_version = "HTTP/1.1"

        # -- response helpers ------------------------------------------------------

        def _json(self, status: int, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _error(self, exc: Exception) -> None:
            status, code, message, retryable = describe(exc)
            self._json(status, {"error": {"code": code, "message": message, "retryable": retryable}})

        def _too_large_response(self) -> None:
            self.close_connection = True
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                "error": {
                    "code": "invalid_request",
                    "message": f"request body exceeds {MAX_BODY_BYTES} bytes",
                    "retryable": False,
                },
            })

        # -- request helpers ---------------------------------------------------------

        def _actor(self):
            header = self.headers.get("Authorization", "")
            return board.authenticate(header[7:]) if header.startswith("Bearer ") else None

        def _require_actor(self):
            actor = self._actor()
            if not actor:
                self._json(HTTPStatus.UNAUTHORIZED, {
                    "error": {"code": "unauthorized", "message": "Bearer token required", "retryable": False},
                })
            return actor

        def _content_length(self) -> int:
            try:
                return int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                return 0

        def _body(self):
            length = self._content_length()
            if length > MAX_BODY_BYTES:
                raise _TooLarge()
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw) if raw else {}

        def _route(self):
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
            return parts, parse_qs(parsed.query)

        # -- GET ----------------------------------------------------------------------

        def do_GET(self):
            parts, query = self._route()
            if not parts:
                body = files("local_board").joinpath("static/index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parts == ["mcp"]:
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            actor = self._require_actor()
            if actor is None:
                return
            try:
                result = self._dispatch_get(actor, parts, query)
                self._json(200, result)
            except Exception as exc:
                self._error(exc)

        def _dispatch_get(self, actor, parts, query):
            if parts == ["api", "me"]:
                return board.get_actor(actor["id"])
            if parts == ["api", "board"]:
                return board.board_context()
            if parts == ["api", "dashboard"]:
                return board.dashboard()
            if len(parts) == 3 and parts[:2] == ["api", "issues"]:
                return board.get_issue(board.resolve_issue(parts[2]))
            if parts == ["api", "activity"]:
                limit = query.get("limit", [None])[0]
                return board.activity(limit=int(limit)) if limit is not None else board.activity()
            raise KeyError("route not found")

        # -- POST ---------------------------------------------------------------------

        def do_POST(self):
            actor = self._require_actor()
            if actor is None:
                return
            try:
                data = self._body()
            except _TooLarge:
                self._too_large_response()
                return
            try:
                parts, _ = self._route()
                if parts == ["mcp"]:
                    self._handle_mcp(actor, data)
                    return
                board.require_role(actor["id"], "admin", "member")
                result = self._dispatch_post(actor, parts, data)
                self._json(HTTPStatus.CREATED, result)
            except Exception as exc:
                self._error(exc)

        def _handle_mcp(self, actor, data):
            from .mcp import handle

            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            accept = self.headers.get("Accept", "")
            if content_type != "application/json":
                self._json(415, {"error": {
                    "code": "invalid_request",
                    "message": "MCP requires Content-Type: application/json",
                    "retryable": False,
                }})
                return
            if "application/json" not in accept or "text/event-stream" not in accept:
                self._json(406, {"error": {
                    "code": "invalid_request",
                    "message": "MCP requires Accept: application/json, text/event-stream",
                    "retryable": False,
                }})
                return
            if isinstance(data, list):
                actor_id = actor["id"]
                responses = [item for entry in data if (item := handle(board, actor_id, entry)) is not None]
                self._empty(202) if not responses else self._json(200, responses)
                return
            response = handle(board, actor["id"], data)
            self._empty(202) if response is None else self._json(200, response)

        def _dispatch_post(self, actor, parts, data):
            if parts == ["api", "issues"]:
                return board.create_issue(actor["id"], **data)
            if len(parts) == 4 and parts[:2] == ["api", "issues"]:
                issue_id = board.resolve_issue(parts[2])
                action = parts[3]
                if action == "claim":
                    return board.claim_issue(actor["id"], issue_id, **data)
                if action == "release":
                    return board.release_issue(actor["id"], issue_id, **data)
                if action == "comments":
                    return board.add_comment(actor["id"], issue_id, **data)
                if action == "dependencies":
                    depends_on_id = board.resolve_issue(data["depends_on"])
                    return board.add_dependency(actor["id"], issue_id, depends_on_id)
                if action == "git-links":
                    return board.add_git_link(actor["id"], issue_id, **data)
                raise KeyError("route not found")
            if parts == ["api", "milestones"]:
                return board.create_milestone(actor["id"], **data)
            if parts == ["api", "labels"]:
                return board.create_label(actor["id"], **data)
            raise KeyError("route not found")

        # -- PATCH ----------------------------------------------------------------------

        def do_PATCH(self):
            actor = self._require_actor()
            if actor is None:
                return
            try:
                data = self._body()
            except _TooLarge:
                self._too_large_response()
                return
            try:
                board.require_role(actor["id"], "admin", "member")
                parts, _ = self._route()
                result = self._dispatch_patch(actor, parts, data)
                self._json(200, result)
            except Exception as exc:
                self._error(exc)

        def _dispatch_patch(self, actor, parts, data):
            if len(parts) != 3:
                raise KeyError("route not found")
            entity, identifier = parts[1], parts[2]
            if entity == "issues":
                return board.update_issue(actor["id"], board.resolve_issue(identifier), **data)
            if entity == "comments":
                return board.update_comment(actor["id"], int(identifier), **data)
            if entity == "labels":
                return board.update_label(actor["id"], int(identifier), **data)
            if entity == "milestones":
                return board.update_milestone(actor["id"], int(identifier), **data)
            if entity == "git-links":
                return board.update_git_link(actor["id"], int(identifier), **data)
            raise KeyError("route not found")

        # -- DELETE -----------------------------------------------------------------

        def do_DELETE(self):
            actor = self._require_actor()
            if actor is None:
                return
            try:
                data = self._body()
            except _TooLarge:
                self._too_large_response()
                return
            try:
                board.require_role(actor["id"], "admin", "member")
                parts, _ = self._route()
                result = self._dispatch_delete(actor, parts, data)
                self._json(200, result)
            except Exception as exc:
                self._error(exc)

        def _dispatch_delete(self, actor, parts, data):
            if len(parts) == 3:
                entity, identifier = parts[1], parts[2]
                if entity == "comments":
                    return board.delete_comment(actor["id"], int(identifier))
                if entity == "labels":
                    return board.delete_label(actor["id"], int(identifier))
                if entity == "milestones":
                    return board.delete_milestone(actor["id"], int(identifier))
                if entity == "git-links":
                    return board.delete_git_link(actor["id"], int(identifier))
            if len(parts) == 4 and parts[:2] == ["api", "issues"] and parts[3] == "dependencies":
                issue_id = board.resolve_issue(parts[2])
                depends_on_id = board.resolve_issue(data["depends_on"])
                return board.remove_dependency(actor["id"], issue_id, depends_on_id)
            raise KeyError("route not found")

        def log_message(self, fmt, *args):
            print(f"[local-board] {fmt % args}")

    return Handler


def serve(board: Board, host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Local Board: http://{host}:{port}")
    ThreadingHTTPServer((host, port), make_handler(board)).serve_forever()
