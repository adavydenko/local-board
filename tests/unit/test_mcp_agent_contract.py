import tempfile
import unittest
from pathlib import Path

from local_board.db import Board
from local_board.mcp import READ_TOOLS, call_tool, handle, schemas


class AgentContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db"); self.board.init()
        self.agent = self.board.create_actor("coding-agent")
        self.reviewer = self.board.create_actor("human-reviewer", "human")
        self.project = self.board.create_project(self.agent["id"], "AGT", "Agent project")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool_name, **arguments):
        return call_tool(self.board, self.agent["id"], tool_name, arguments)

    def test_discovery_and_stable_identifier_lifecycle(self):
        self.assertEqual(self.call("whoami")["name"], "coding-agent")
        self.assertEqual({actor["name"] for actor in self.call("list_actors")}, {"coding-agent", "human-reviewer"})
        project = self.call("get_project_context", project="AGT")
        self.assertEqual(project["key"], "AGT")
        self.assertEqual(len(project["workflows"]), 5)
        self.assertEqual(len(self.call("list_workflows", project="AGT")), 5)

        issue = self.call("create_issue", project="AGT", title="Implement contract", reviewer="human-reviewer")
        self.assertEqual(issue["identifier"], "AGT-1")
        claimed = self.call("claim_issue", issue="AGT-1", expected_revision=issue["revision"])
        comment = self.call("add_comment", issue="AGT-1", body="Working on it")
        checklist = self.call("add_checklist_item", issue="AGT-1", text="Add tests")
        label = self.call("create_label", project="AGT", key="mcp", name="MCP")
        self.call("add_label", issue="AGT-1", label="mcp")
        attachment = self.call("add_attachment", issue="AGT-1", name="design", path="docs/design.md")
        link = self.call("add_git_link", issue="AGT-1", link_kind="branch", ref="AGT-1-contract")

        context = self.call("get_issue_context", issue="AGT-1")
        self.assertEqual(context["assignee"], "coding-agent")
        self.assertEqual(context["reviewer"], "human-reviewer")
        self.assertEqual(context["comments"][0]["body"], "Working on it")
        self.assertEqual(context["checklist"][0]["text"], "Add tests")
        self.assertEqual(context["labels"][0]["id"], label["id"])
        self.assertEqual(context["attachments"][0]["id"], attachment["id"])
        self.assertEqual(context["git_links"][0]["id"], link["id"])
        self.assertIn("todo", context["available_transitions"])
        self.assertIn("todo", self.call("get_available_transitions", issue="AGT-1")["transitions"])
        self.assertGreater(len(context["activity"]), 1)

        self.call("update_comment", comment_id=comment["id"], body="Ready")
        self.call("update_checklist_item", item_id=checklist["id"], completed=True)
        self.call("remove_label", issue="AGT-1", label="mcp")
        self.call("delete_attachment", attachment_id=attachment["id"])
        self.call("delete_git_link", link_id=link["id"])
        released = self.call("release_issue", issue="AGT-1", expected_revision=claimed["revision"])
        self.assertIsNone(released["assignee_id"])

    def test_dependency_context_and_removal(self):
        first = self.call("create_issue", project="AGT", title="First")
        second = self.call("create_issue", project="AGT", title="Second")
        self.call("add_dependency", issue=second["identifier"], depends_on=first["identifier"])
        context = self.call("get_issue_context", issue=second["identifier"])
        self.assertEqual(context["dependencies"][0]["identifier"], first["identifier"])
        self.assertTrue(context["blocked"])
        second = self.call("transition_issue", issue=second["identifier"], status="todo", expected_revision=second["revision"])
        blocked = handle(self.board, self.agent["id"], {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "transition_issue", "arguments": {"issue": second["identifier"], "status": "in_progress", "expected_revision": second["revision"]}}})
        self.assertEqual(blocked["result"]["structuredContent"]["error"]["code"], "blocked")
        self.call("remove_dependency", issue=second["identifier"], depends_on=first["identifier"])
        self.assertEqual(self.call("get_issue_context", issue=second["identifier"])["dependencies"], [])

    def test_errors_are_machine_readable(self):
        issue = self.call("create_issue", project="AGT", title="Conflict")
        self.call("claim_issue", issue=issue["identifier"], expected_revision=issue["revision"])
        response = handle(self.board, self.reviewer["id"], {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claim_issue", "arguments": {"issue": issue["identifier"], "expected_revision": issue["revision"]}}})
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "conflict")
        self.assertTrue(error["retryable"])

    def test_tool_schemas_expose_enums_and_stable_refs(self):
        tools = {item["name"]: item for item in schemas()}
        self.assertIn("oneOf", tools["get_issue_context"]["inputSchema"]["properties"]["issue"])
        self.assertEqual(len(tools["get_issue_context"]["inputSchema"]["properties"]["issue"]["oneOf"]), 1)
        self.assertEqual(tools["create_issue"]["inputSchema"]["properties"]["priority"]["enum"], ["none", "low", "medium", "high", "urgent"])
        self.assertIn("expected_revision", tools["transition_issue"]["inputSchema"]["required"])
        self.assertEqual(set(tools["create_issue"]["x-errorResponses"]), {"not_found", "conflict", "invalid_transition", "blocked", "unauthorized", "retryable"})
        self.assertIn("examples", tools["create_issue"]["inputSchema"])

    def test_single_resource_discovery_and_related_object_keys(self):
        self.call("create_milestone", project="AGT", key="m1", name="Milestone one")
        self.call("create_label", project="AGT", key="api", name="API")
        self.assertEqual(self.call("get_workflow", project="AGT", issue_type="feature")["issue_type"], "feature")
        self.assertEqual(self.call("get_milestone", project="AGT", milestone="m1")["name"], "Milestone one")
        self.assertEqual(self.call("get_label", project="AGT", label="api")["name"], "API")

        issue = self.call("create_issue", project="AGT", title="Opaque related keys")
        comment = self.call("add_comment", issue=issue["identifier"], body="Before")
        item = self.call("add_checklist_item", issue=issue["identifier"], text="Finish")
        attachment = self.call("add_attachment", issue=issue["identifier"], name="old", path="old.txt")
        link = self.call("add_git_link", issue=issue["identifier"], link_kind="branch", ref="old")
        context = self.call("get_issue_context", issue=issue["identifier"])
        self.call("update_comment", comment=context["comments"][0]["key"], body="After")
        completed = self.call("complete_checklist_item", item=context["checklist"][0]["key"])
        self.assertTrue(completed["completed"])
        self.call("update_attachment", attachment=context["attachments"][0]["key"], name="new")
        self.call("update_git_link", link=context["git_links"][0]["key"], link_kind="commit", ref="abc123")
        refreshed = self.call("get_issue_context", issue=issue["identifier"])
        self.assertEqual(refreshed["comments"][0]["body"], "After")
        self.assertEqual(refreshed["attachments"][0]["name"], "new")
        self.assertEqual(refreshed["git_links"][0]["kind"], "commit")
        self.assertEqual({comment["id"], item["id"], attachment["id"], link["id"]}, {1})

    def test_update_delete_label_and_dependency(self):
        self.call("create_label", project="AGT", key="api", name="API")
        updated = self.call("update_label", project="AGT", label="api", name="Public API")
        self.assertEqual(updated["name"], "Public API")
        self.assertTrue(self.call("delete_label", project="AGT", label="api")["deleted"])
        first = self.call("create_issue", project="AGT", title="First")
        second = self.call("create_issue", project="AGT", title="Second")
        self.call("add_dependency", issue=second["identifier"], depends_on=first["identifier"])
        changed = self.call("update_dependency", issue=second["identifier"], depends_on=first["identifier"], new_relation="related")
        self.assertEqual(changed["relation"], "related")

    def test_viewer_is_read_only_and_activity_tools_are_immutable(self):
        viewer = self.board.create_actor("audit-viewer", role="viewer")
        names = {tool["name"] for tool in schemas()}
        self.assertNotIn("update_activity", names)
        self.assertNotIn("delete_activity", names)
        response = handle(self.board, viewer["id"], {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "create_project", "arguments": {"key": "NOPE", "name": "Denied"}}})
        self.assertEqual(response["result"]["structuredContent"]["error"]["code"], "unauthorized")
        read = handle(self.board, viewer["id"], {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})
        self.assertFalse(read["result"].get("isError", False))

    def test_admin_can_provision_and_rotate_subagent_credentials(self):
        created = self.call("create_actor", name="subagent-one", kind="agent", role="member")
        self.assertEqual(self.board.authenticate(created["token"])["name"], "subagent-one")
        self.assertEqual(self.board.activity("actor", created["id"])[0]["action"], "created")

        rotated = self.call("rotate_actor_token", actor="subagent-one")
        self.assertIsNone(self.board.authenticate(created["token"]))
        self.assertEqual(self.board.authenticate(rotated["token"])["id"], created["id"])
        self.assertEqual(self.board.activity("actor", created["id"])[0]["action"], "token_rotated")

        denied = handle(self.board, self.reviewer["id"], {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "create_actor", "arguments": {"name": "forbidden"}}})
        self.assertEqual(denied["result"]["structuredContent"]["error"]["code"], "unauthorized")

        member_tools = handle(self.board, self.reviewer["id"], {"jsonrpc": "2.0", "id": 12, "method": "tools/list"})["result"]["tools"]
        self.assertNotIn("create_actor", {item["name"] for item in member_tools})
        viewer = self.board.create_actor("credential-auditor", role="viewer")
        viewer_tools = handle(self.board, viewer["id"], {"jsonrpc": "2.0", "id": 13, "method": "tools/list"})["result"]["tools"]
        self.assertEqual({item["name"] for item in viewer_tools}, READ_TOOLS)
