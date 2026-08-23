"""End-to-end: an agent bootstraps from MCP initialize alone and completes work."""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from local_board.config import ConfigService, default_config, load_config
from local_board.db import Board
from local_board.web import make_handler

from http.server import ThreadingHTTPServer


class BootstrapAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.board = Board(root / "state" / "board.db")
        self.board.init()
        config_path = root / "project.toml"
        config_path.write_text(default_config("Application", "APP"), encoding="utf-8")
        ConfigService(self.board).apply(load_config(config_path))
        self.admin = self.board.create_actor("coordinator", "agent")
        self.worker = self.board.create_actor("worker", "agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _call(self, token, payload):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def _tool(self, token, name, arguments, request_id=1):
        response = self._call(token, {
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = response["result"]
        self.assertFalse(result.get("isError"), result)
        return result["structuredContent"]

    def test_agent_bootstraps_without_discovery_calls(self):
        # 1. initialize alone must give identity and the board snapshot.
        initialized = self._call(self.worker["token"], {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "e2e", "version": "0"}},
        })
        instructions = initialized["result"]["instructions"]
        self.assertIn("worker", instructions)
        self.assertIn("APP", instructions)
        self.assertIn("In Review", instructions)

        # 2. The planner creates work; the worker finds, claims, and finishes it.
        created = self._tool(self.admin["token"], "create_issue", {
            "title": "Ship the feature",
            "description": "Acceptance:\n- [ ] implemented\n- [ ] tested",
            "labels": ["review_required"],
        })
        identifier = created["identifier"]

        issues = self._tool(self.worker["token"], "list_issues", {"label": "review_required"})
        self.assertEqual([issue["identifier"] for issue in issues], [identifier])

        claimed = self._tool(self.worker["token"], "claim_issue", {
            "issue": identifier, "expected_revision": created["revision"],
        })
        started = self._tool(self.worker["token"], "update_issue", {
            "issue": identifier, "expected_revision": claimed["revision"], "status": "In Progress",
        })
        self._tool(self.worker["token"], "add_comment", {
            "issue": identifier, "body": "Implemented; needs review per label.",
        })
        reviewed = self._tool(self.worker["token"], "update_issue", {
            "issue": identifier,
            "expected_revision": self._tool(self.worker["token"], "get_issue", {"issue": identifier})["revision"],
            "status": "In Review",
        })
        self.assertEqual(reviewed["status"], "In Review")
        self.assertEqual(started["assignee"], "worker")

        # 3. A stale revision is a structured conflict, not a dropped connection.
        response = self._call(self.worker["token"], {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "update_issue", "arguments": {
                "issue": identifier, "expected_revision": 1, "status": "Done",
            }},
        })
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "conflict")
        self.assertTrue(error["retryable"])


if __name__ == "__main__":
    unittest.main()
