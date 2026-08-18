import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_board.db import Board
from local_board.config import ConfigService, default_config, load_config
from local_board.doctor import run_doctor
from local_board.web import make_handler


class HttpIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
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

    def request(self, path, *, body=None, token=True):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {self.actor['token']}"
        data = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=data, headers=headers), timeout=3) as response:
            return response.status, json.load(response)

    def test_authenticated_http_and_mcp_lifecycle(self):
        status, project = self.request("/api/projects", body={"key": "HTTP", "name": "HTTP project"})
        self.assertEqual(status, 201)
        status, issue = self.request("/api/issues", body={"project_id": project["id"], "title": "Transport test"})
        self.assertEqual(status, 201)
        status, dashboard = self.request("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["issues"][0]["id"], issue["id"])
        status, response = self.request("/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertGreater(len(response["result"]["tools"]), 10)
        status, claimed = self.request("/mcp", body={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "claim_issue", "arguments": {"issue": issue["identifier"], "expected_revision": issue["revision"]}}})
        self.assertEqual(status, 200)
        self.assertEqual(claimed["result"]["structuredContent"]["assignee_id"], self.actor["id"])
        status, stale = self.request("/mcp", body={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "claim_issue", "arguments": {"issue": issue["identifier"], "expected_revision": issue["revision"]}}})
        self.assertTrue(stale["result"]["isError"])

    def test_http_requires_a_valid_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/dashboard", token=False)
        self.assertEqual(caught.exception.code, 401)

    def test_mcp_notification_returns_empty_accepted_response(self):
        headers = {"Authorization": f"Bearer {self.actor['token']}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        request = Request(self.url + "/mcp", data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), headers=headers)
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(response.read(), b"")

    def test_mcp_rejects_incompatible_accept_header(self):
        headers = {"Authorization": f"Bearer {self.actor['token']}", "Content-Type": "application/json", "Accept": "application/json"}
        request = Request(self.url + "/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(), headers=headers)
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 406)

    def test_online_doctor_checks_auth_initialize_and_tools(self):
        config_path = Path(self.tmp.name) / "project.toml"
        config_path.write_text(default_config("HTTP", "DHTTP"))
        ConfigService(self.board).apply(load_config(config_path))
        result = run_doctor(self.board, config_path, url=self.url + "/mcp", token=self.actor["token"])
        self.assertTrue(result["ok"])
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["mcp_initialize"], "pass")
        self.assertEqual(statuses["mcp_tools"], "pass")
