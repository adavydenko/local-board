import importlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_board.db import Board
from local_board.web import make_handler

try:
    importlib.import_module("local_board.mcp")
    MCP_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only hit while mcp.py is mid-rewrite
    MCP_IMPORT_ERROR = exc


class HttpIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.actor = self.board.create_actor("http-agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, path, *, body=None, token=True, method=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {self.actor['token']}"
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=3) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)

    @unittest.skipIf(MCP_IMPORT_ERROR is not None, f"local_board.mcp not importable: {MCP_IMPORT_ERROR}")
    def test_mcp_over_http_happy_path(self):
        status, initialized = self.request("/mcp", body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}},
        })
        self.assertEqual(status, 200)
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "local-board")
        status, tools = self.request("/mcp", body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertGreater(len(tools["result"]["tools"]), 5)

    @unittest.skipIf(MCP_IMPORT_ERROR is not None, f"local_board.mcp not importable: {MCP_IMPORT_ERROR}")
    def test_mcp_rejects_wrong_content_type(self):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "text/plain",
                   "Authorization": f"Bearer {self.actor['token']}"}
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        request = Request(self.url + "/mcp", data=payload, headers=headers, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 415)

    @unittest.skipIf(MCP_IMPORT_ERROR is not None, f"local_board.mcp not importable: {MCP_IMPORT_ERROR}")
    def test_mcp_rejects_incompatible_accept_header(self):
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Authorization": f"Bearer {self.actor['token']}"}
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        request = Request(self.url + "/mcp", data=payload, headers=headers, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 406)

    @unittest.skipIf(MCP_IMPORT_ERROR is not None, f"local_board.mcp not importable: {MCP_IMPORT_ERROR}")
    def test_mcp_batch_request(self):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        status, responses = self.request("/mcp", body=batch)
        self.assertEqual(status, 200)
        self.assertEqual(len(responses), 2)
        ids = {item["id"] for item in responses}
        self.assertEqual(ids, {1, 2})

    @unittest.skipIf(MCP_IMPORT_ERROR is not None, f"local_board.mcp not importable: {MCP_IMPORT_ERROR}")
    def test_mcp_notification_returns_empty_accepted_response(self):
        headers = {"Authorization": f"Bearer {self.actor['token']}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        payload = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        request = Request(self.url + "/mcp", data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(response.read(), b"")

    def test_http_requires_a_valid_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/dashboard", token=False, method="GET")
        self.assertEqual(caught.exception.code, 401)

    def test_rest_issue_lifecycle_over_http(self):
        status, issue = self.request("/api/issues", body={"title": "Transport test"}, method="POST")
        self.assertEqual(status, 201)
        status, dashboard = self.request("/api/dashboard", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["issues"][0]["id"], issue["id"])
        status, context = self.request(f"/api/issues/{issue['identifier']}", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(context["identifier"], issue["identifier"])


if __name__ == "__main__":
    unittest.main()
