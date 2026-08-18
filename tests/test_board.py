import json
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board
from local_board.mcp import handle, schemas


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.actor = self.board.create_actor("agent-one")

    def tearDown(self): self.tmp.cleanup()

    def test_full_issue_lifecycle(self):
        project = self.board.create_project(self.actor["id"], "APP", "Application")
        issue = self.board.create_issue(self.actor["id"], project["id"], "Implement auth", "feature", "**Securely** authenticate", "high")
        self.assertEqual(issue["identifier"], "APP-1")
        issue = self.board.transition_issue(self.actor["id"], issue["id"], "todo")
        self.assertEqual(issue["status"], "todo")
        self.board.add_related(self.actor["id"], issue["id"], "comment", body="Ready")
        self.board.add_related(self.actor["id"], issue["id"], "checklist", text="Add tests")
        self.assertEqual(len(self.board.get_issue(issue["id"])["checklist"]), 1)
        self.assertGreaterEqual(len(self.board.activity("issue", issue["id"])), 4)
        event = self.board.activity("issue", issue["id"])[0]
        with self.assertRaisesRegex(PermissionError, "immutable"):
            self.board.update_activity(event["id"], action="corrected")
        with self.assertRaisesRegex(PermissionError, "immutable"):
            self.board.delete_activity(event["id"])

    def test_rejects_invalid_transition(self):
        project = self.board.create_project(self.actor["id"], "WEB", "Web")
        issue = self.board.create_issue(self.actor["id"], project["id"], "Bug")
        with self.assertRaises(ValueError): self.board.transition_issue(self.actor["id"], issue["id"], "done")

    def test_authentication(self):
        self.assertEqual(self.board.authenticate(self.actor["token"])["name"], "agent-one")
        self.assertIsNone(self.board.authenticate("wrong"))

    def test_mcp_contract(self):
        response = handle(self.board, self.actor["id"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertGreater(len(response["result"]["tools"]), 10)
        self.assertTrue(all("inputSchema" in tool for tool in schemas()))
        project = handle(self.board, self.actor["id"], {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "create_project", "arguments": {"key": "MCP", "name": "MCP"}}})
        self.assertFalse(project["result"].get("isError", False))
        json.dumps(project)


if __name__ == "__main__": unittest.main()
