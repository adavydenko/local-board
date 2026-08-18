import json
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board, ConflictError
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
        self.assertEqual(self.board.update_activity(event["id"], action="corrected")["action"], "corrected")
        self.assertTrue(self.board.delete_activity(event["id"])["deleted"])

    def test_rejects_invalid_transition(self):
        project = self.board.create_project(self.actor["id"], "WEB", "Web")
        issue = self.board.create_issue(self.actor["id"], project["id"], "Bug")
        with self.assertRaises(ValueError): self.board.transition_issue(self.actor["id"], issue["id"], "done")

    def test_revision_protects_updates_and_transitions(self):
        project = self.board.create_project(self.actor["id"], "REV", "Revisions")
        issue = self.board.create_issue(self.actor["id"], project["id"], "Versioned")
        updated = self.board.update_issue(self.actor["id"], issue["id"], expected_revision=issue["revision"], title="Updated")
        self.assertEqual(updated["revision"], issue["revision"] + 1)
        with self.assertRaises(ConflictError):
            self.board.update_issue(self.actor["id"], issue["id"], expected_revision=issue["revision"], title="Stale")
        transitioned = self.board.transition_issue(self.actor["id"], issue["id"], "todo", expected_revision=updated["revision"])
        self.assertEqual(transitioned["revision"], updated["revision"] + 1)

    def test_claim_renew_and_release(self):
        project = self.board.create_project(self.actor["id"], "CLM", "Claims")
        issue = self.board.create_issue(self.actor["id"], project["id"], "Claimable")
        claimed = self.board.claim_issue(self.actor["id"], issue["id"], issue["revision"], lease_seconds=60)
        self.assertEqual(claimed["assignee_id"], self.actor["id"])
        self.assertIsNotNone(claimed["claim_expires_at"])
        renewed = self.board.claim_issue(self.actor["id"], issue["id"], claimed["revision"], lease_seconds=120)
        released = self.board.release_issue(self.actor["id"], issue["id"], renewed["revision"])
        self.assertIsNone(released["assignee_id"])
        self.assertIsNone(released["claim_expires_at"])

    def test_cross_project_milestones_labels_and_dependencies_are_rejected(self):
        first = self.board.create_project(self.actor["id"], "ONE", "One")
        second = self.board.create_project(self.actor["id"], "TWO", "Two")
        milestone = self.board.create_milestone(self.actor["id"], second["id"], "Foreign")
        label = self.board.create_label(self.actor["id"], second["id"], "foreign")
        one = self.board.create_issue(self.actor["id"], first["id"], "One")
        two = self.board.create_issue(self.actor["id"], second["id"], "Two")
        with self.assertRaises(ValueError):
            self.board.update_issue(self.actor["id"], one["id"], expected_revision=one["revision"], milestone_id=milestone["id"])
        with self.assertRaises(ValueError):
            self.board.add_label(self.actor["id"], one["id"], label["id"])
        with self.assertRaises(ValueError):
            self.board.add_related(self.actor["id"], one["id"], "dependency", depends_on_id=two["id"])

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
