import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_board import __version__
from local_board.db import Board
from local_board.web import make_handler


class WebUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.actor = self.board.create_actor("web-agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, *, body=None, token=True, headers=None, parse_json=True):
        merged = {"Content-Type": "application/json"}
        if token:
            merged["Authorization"] = f"Bearer {self.actor['token']}"
        merged.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, headers=merged, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw and parse_json else raw)
        except HTTPError as error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw and parse_json else raw)

    def test_auth_is_required_for_api_routes(self):
        status, body = self.request("GET", "/api/dashboard", token=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_health_endpoint_requires_no_auth(self):
        status, body = self.request("GET", "/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], __version__)

    def test_root_serves_static_index_without_auth(self):
        status, body = self.request("GET", "/", token=False, parse_json=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())

    def test_dashboard_shape(self):
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(set(dashboard), {"board", "issues", "actors", "activity"})
        self.assertEqual(dashboard["board"]["prefix"], "APP")
        self.assertEqual(dashboard["issues"], [])
        self.assertEqual(dashboard["actors"][0]["name"], "web-agent")

    def test_create_issue_via_post(self):
        status, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        self.assertEqual(status, 201)
        self.assertEqual(issue["title"], "Ship it")
        self.assertEqual(issue["identifier"], "APP-1")

    def test_patch_status_with_expected_revision(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        status, updated = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"status": "Todo", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "Todo")
        self.assertEqual(updated["revision"], issue["revision"] + 1)

    def test_stale_revision_returns_409_with_json_body(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        status, first = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"priority": "high", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 200)
        status, stale = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"priority": "low", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertTrue(stale["error"]["retryable"])
        self.assertIn("error", stale)

    def test_duplicate_label_name_returns_409_json_not_connection_drop(self):
        status, first = self.request("POST", "/api/labels", body={"name": "bug"})
        self.assertEqual(status, 201)
        status, second = self.request("POST", "/api/labels", body={"name": "bug"})
        self.assertEqual(status, 409)
        self.assertIn("error", second)
        self.assertEqual(second["error"]["code"], "conflict")

    def test_oversized_body_returns_413(self):
        oversized = json.dumps({"title": "x" * 1_000_001}).encode()
        request = Request(
            self.url + "/api/issues",
            data=oversized,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.actor['token']}",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 413)
        body = json.loads(caught.exception.read())
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
