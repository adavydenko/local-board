from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urlparse

from .db import AuthorizationError, Board, ConflictError
from .mcp import handle


def make_handler(board: Board):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalBoard/0.1"

        def _json(self, status: int, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _empty(self, status: int) -> None:
            self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()

        def _actor(self):
            header = self.headers.get("Authorization", "")
            return board.authenticate(header[7:]) if header.startswith("Bearer ") else None

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def _route(self):
            parsed = urlparse(self.path)
            return [unquote(part) for part in parsed.path.strip("/").split("/") if part], parse_qs(parsed.query)

        def _error(self, exc: Exception) -> None:
            if isinstance(exc, ConflictError): status, code = HTTPStatus.CONFLICT, "conflict"
            elif isinstance(exc, AuthorizationError): status, code = HTTPStatus.FORBIDDEN, "forbidden"
            elif isinstance(exc, KeyError): status, code = HTTPStatus.NOT_FOUND, "not_found"
            elif isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)): status, code = HTTPStatus.BAD_REQUEST, "invalid_request"
            else: raise exc
            self._json(status, {"error": {"code": code, "message": str(exc).strip("'")}})

        def _require_actor(self):
            actor = self._actor()
            if not actor: self._json(HTTPStatus.UNAUTHORIZED, {"error": {"code": "unauthorized", "message": "Bearer token required"}})
            return actor

        def do_GET(self):
            parts, query = self._route()
            if not parts:
                body = files("local_board").joinpath("static/index.html").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            actor = self._require_actor()
            if not actor: return
            try:
                if parts == ["api", "me"]: return self._json(200, board.get_actor(actor["id"]))
                if parts == ["api", "actors"]: return self._json(200, board.list_actors())
                if parts == ["api", "dashboard"]:
                    projects = board.list_projects()
                    project_ref = query.get("project", [projects[0]["key"] if projects else None])[0]
                    context = board.project_context(project_ref) if project_ref else None
                    project_id = context["id"] if context else None
                    return self._json(200, {"me": board.get_actor(actor["id"]), "projects": projects, "project": context, "issues": board.list_issues(project_id=project_id), "actors": board.list_actors(), "activity": board.activity(limit=50)})
                if len(parts) == 3 and parts[:2] == ["api", "projects"]: return self._json(200, board.project_context(parts[2]))
                if len(parts) == 3 and parts[:2] == ["api", "issues"]: return self._json(200, board.get_issue_context(parts[2]))
                if parts == ["mcp"]:
                    self.send_response(405); self.send_header("Allow", "POST"); self.send_header("Content-Length", "0"); self.end_headers(); return
                return self._json(404, {"error": {"code": "not_found", "message": "route not found"}})
            except Exception as exc: return self._error(exc)

        def do_POST(self):
            actor = self._require_actor()
            if not actor: return
            try:
                data = self._body(); parts, _ = self._route()
                if parts == ["mcp"]:
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip(); accept = self.headers.get("Accept", "")
                    if content_type != "application/json": return self._json(415, {"error": "MCP requires Content-Type: application/json"})
                    if "application/json" not in accept or "text/event-stream" not in accept: return self._json(406, {"error": "MCP requires Accept: application/json, text/event-stream"})
                    if isinstance(data, list):
                        responses = [response for item in data if (response := handle(board, actor["id"], item)) is not None]
                        return self._empty(202) if not responses else self._json(200, responses)
                    response = handle(board, actor["id"], data)
                    return self._empty(202) if response is None else self._json(200, response)
                board.require_role(actor["id"], "admin", "member")
                if parts == ["api", "projects"]: result = board.create_project(actor["id"], **data)
                elif parts == ["api", "releases"]:
                    if "project" in data: data["project_id"] = board.resolve_project(data.pop("project"))
                    result = board.create_release(actor["id"], **data)
                elif len(parts) == 4 and parts[:2] == ["api", "releases"] and parts[3] == "transition":
                    result = board.transition_release(actor["id"], int(parts[2]), **data)
                elif parts == ["api", "issues"]:
                    if "project" in data: data["project_id"] = board.resolve_project(data.pop("project"))
                    result = board.create_issue(actor["id"], **data)
                elif len(parts) == 4 and parts[:2] == ["api", "issues"]:
                    issue_id = board.resolve_issue(parts[2]); action = parts[3]
                    if action == "transition": result = board.transition_issue(actor["id"], issue_id, **data)
                    elif action == "claim": result = board.claim_issue(actor["id"], issue_id, **data)
                    elif action == "release": result = board.release_issue(actor["id"], issue_id, **data)
                    elif action == "comments": result = board.add_related(actor["id"], issue_id, "comment", **data)
                    elif action == "checklist": result = board.add_related(actor["id"], issue_id, "checklist", **data)
                    elif action == "git-links": result = board.add_related(actor["id"], issue_id, "git_link", **data)
                    elif action == "dependencies": data["depends_on_id"] = board.resolve_issue(data.pop("depends_on")); result = board.add_related(actor["id"], issue_id, "dependency", **data)
                    elif action == "labels": result = board.add_label(actor["id"], issue_id, int(data["label_id"]))
                    else: raise KeyError("route not found")
                else: raise KeyError("route not found")
                return self._json(HTTPStatus.CREATED, result)
            except Exception as exc: return self._error(exc)

        def do_PATCH(self):
            actor = self._require_actor()
            if not actor: return
            try:
                board.require_role(actor["id"], "admin", "member")
                data = self._body(); parts, _ = self._route()
                if len(parts) == 3 and parts[:2] == ["api", "issues"]: result = board.update_issue(actor["id"], board.resolve_issue(parts[2]), **data)
                elif len(parts) == 3 and parts[:2] == ["api", "comments"]: result = board.update_comment(actor["id"], int(parts[2]), **data)
                elif len(parts) == 3 and parts[:2] == ["api", "checklist"]: result = board.update_checklist_item(actor["id"], int(parts[2]), **data)
                else: raise KeyError("route not found")
                return self._json(200, result)
            except Exception as exc: return self._error(exc)

        def do_DELETE(self):
            actor = self._require_actor()
            if not actor: return
            try:
                board.require_role(actor["id"], "admin", "member")
                data = self._body(); parts, _ = self._route()
                if len(parts) == 3 and parts[:2] == ["api", "comments"]: result = board.delete_comment(actor["id"], int(parts[2]))
                elif len(parts) == 3 and parts[:2] == ["api", "checklist"]: result = board.delete_checklist_item(actor["id"], int(parts[2]))
                elif len(parts) == 3 and parts[:2] == ["api", "attachments"]: result = board.delete_attachment(actor["id"], int(parts[2]))
                elif len(parts) == 3 and parts[:2] == ["api", "git-links"]: result = board.delete_git_link(actor["id"], int(parts[2]))
                elif len(parts) == 4 and parts[:2] == ["api", "issues"] and parts[3] == "labels": result = board.remove_label(actor["id"], board.resolve_issue(parts[2]), int(data["label_id"]))
                elif len(parts) == 4 and parts[:2] == ["api", "issues"] and parts[3] == "dependencies": result = board.remove_dependency(actor["id"], board.resolve_issue(parts[2]), board.resolve_issue(data["depends_on"]), data.get("relation", "blocks"))
                else: raise KeyError("route not found")
                return self._json(200, result)
            except Exception as exc: return self._error(exc)

        def log_message(self, fmt, *args):
            print(f"[local-board] {fmt % args}")
    return Handler


def serve(board: Board, host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Local Board: http://{host}:{port}")
    ThreadingHTTPServer((host, port), make_handler(board)).serve_forever()
