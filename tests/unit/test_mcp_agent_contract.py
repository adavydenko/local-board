import tempfile
import unittest
from pathlib import Path

from local_board.db import Board
from local_board.mcp import (
    ADMIN_TOOLS,
    CORRECTION_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    call_tool,
    handle,
    schemas,
)


class AgentContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        bootstrap = self.board.create_actor("bootstrap-admin")
        self.admin = self.call_as(bootstrap["id"], "create_actor", name="admin-agent", role="admin")
        self.member = self.call_as(self.admin["id"], "create_actor", name="member-agent", role="member")
        self.viewer = self.call_as(self.admin["id"], "create_actor", name="viewer-agent", role="viewer")

    def tearDown(self):
        self.tmp.cleanup()

    def call_as(self, actor_id, tool_name, **arguments):
        return call_tool(self.board, actor_id, tool_name, arguments)

    def call(self, tool_name, **arguments):
        return self.call_as(self.member["id"], tool_name, **arguments)

    def rpc(self, actor_id, method, params=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            request["params"] = params
        return handle(self.board, actor_id, request)

    # -- initialize -------------------------------------------------------------

    def test_initialize_returns_instructions(self):
        response = self.rpc(self.member["id"], "initialize")
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"]["name"], "local-board")
        instructions = result["instructions"]
        self.assertIn("member-agent", instructions)
        self.assertIn("APP", instructions)
        self.assertIn("Backlog", instructions)
        self.assertIn("Todo", instructions)
        self.assertIn("Done", instructions)

    def test_initialize_handles_unconfigured_board(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            board = Board(Path(tmp.name) / "board.db")
            board.init()
            actor = board.create_actor("solo-admin")
            response = handle(board, actor["id"], {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            instructions = response["result"]["instructions"]
            self.assertIn("solo-admin", instructions)
            self.assertIn("not been configured", instructions)
        finally:
            tmp.cleanup()

    def test_notifications_initialized_and_ping(self):
        self.assertIsNone(self.rpc(self.member["id"], "notifications/initialized"))
        self.assertEqual(self.rpc(self.member["id"], "ping")["result"], {})

    def test_unknown_method_returns_json_rpc_error(self):
        response = self.rpc(self.member["id"], "not/a/method")
        self.assertEqual(response["error"]["code"], -32601)

    # -- tools/list per role ------------------------------------------------------

    def test_tool_counts_and_grouping_per_role(self):
        self.assertEqual(len(READ_TOOLS), 5)
        self.assertEqual(len(WRITE_TOOLS), 11)
        self.assertEqual(len(CORRECTION_TOOLS), 7)
        self.assertEqual(len(ADMIN_TOOLS), 3)

        viewer_tools = schemas("viewer")
        member_tools = schemas("member")
        admin_tools = schemas("admin")
        self.assertEqual(len(viewer_tools), 5)
        self.assertEqual(len(member_tools), 16)
        self.assertEqual(len(admin_tools), 26)

        member_names = {item["name"] for item in member_tools}
        self.assertTrue(CORRECTION_TOOLS.isdisjoint(member_names))
        self.assertTrue(ADMIN_TOOLS.isdisjoint(member_names))

        viewer_response = self.rpc(self.viewer["id"], "tools/list")
        self.assertEqual({item["name"] for item in viewer_response["result"]["tools"]}, READ_TOOLS)
        member_response = self.rpc(self.member["id"], "tools/list")
        self.assertEqual(len(member_response["result"]["tools"]), 16)
        admin_response = self.rpc(self.admin["id"], "tools/list")
        self.assertEqual(len(admin_response["result"]["tools"]), 26)

    # -- full lifecycle via tools/call ---------------------------------------------

    def test_create_claim_update_comment_update_flow(self):
        created = self.call("create_issue", title="Ship the redesign")
        self.assertEqual(created["identifier"], "APP-1")
        self.assertEqual(created["status"], "Backlog")

        claimed = self.call("claim_issue", issue="APP-1", expected_revision=created["revision"])
        self.assertEqual(claimed["assignee"], "member-agent")

        started = self.call(
            "update_issue", issue="APP-1", expected_revision=claimed["revision"], status="In Progress"
        )
        self.assertEqual(started["status"], "In Progress")

        comment = self.call("add_comment", issue="APP-1", body="Working on it")
        self.assertEqual(comment["body"], "Working on it")

        done = self.call(
            "update_issue", issue="APP-1", expected_revision=started["revision"], status="Done"
        )
        self.assertEqual(done["status"], "Done")
        self.assertEqual(done["category"], "completed")

        fetched = self.call("get_issue", issue="APP-1")
        self.assertEqual(fetched["status"], "Done")
        self.assertEqual(fetched["comments"][0]["body"], "Working on it")

    def test_get_issue_by_string_ref(self):
        self.call("create_issue", title="Ref lookup")
        issue = self.call("get_issue", issue="APP-1")
        self.assertEqual(issue["identifier"], "APP-1")
        self.assertEqual(issue["title"], "Ref lookup")

    # -- error mapping --------------------------------------------------------------

    def test_stale_revision_conflict_is_retryable(self):
        created = self.call("create_issue", title="Conflict me")
        self.call("update_issue", issue="APP-1", expected_revision=created["revision"], title="First edit")

        response = self.rpc(
            self.member["id"],
            "tools/call",
            {
                "name": "update_issue",
                "arguments": {
                    "issue": "APP-1",
                    "expected_revision": created["revision"],
                    "title": "Stale edit",
                },
            },
        )
        self.assertTrue(response["result"]["isError"])
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "conflict")
        self.assertTrue(error["retryable"])

    def test_duplicate_label_name_is_conflict(self):
        self.call("create_label", name="backend")
        response = self.rpc(
            self.member["id"],
            "tools/call",
            {"name": "create_label", "arguments": {"name": "backend"}},
        )
        self.assertTrue(response["result"]["isError"])
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "conflict")

    def test_member_calling_correction_tool_is_unauthorized(self):
        label = self.call("create_label", name="frontend")
        response = self.rpc(
            self.member["id"],
            "tools/call",
            {"name": "delete_label", "arguments": {"label": label["id"]}},
        )
        self.assertTrue(response["result"]["isError"])
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "unauthorized")
        self.assertFalse(error["retryable"])

    def test_viewer_calling_write_tool_is_unauthorized(self):
        response = self.rpc(
            self.viewer["id"],
            "tools/call",
            {"name": "create_issue", "arguments": {"title": "Not allowed"}},
        )
        self.assertTrue(response["result"]["isError"])
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "unauthorized")

    def test_viewer_can_still_read(self):
        self.call("create_issue", title="Visible to viewer")
        response = self.rpc(
            self.viewer["id"], "tools/call", {"name": "get_issue", "arguments": {"issue": "APP-1"}}
        )
        self.assertFalse(response["result"].get("isError", False))
        self.assertEqual(response["result"]["structuredContent"]["identifier"], "APP-1")

    def test_admin_can_perform_correction_and_admin_tools(self):
        label = self.call_as(self.admin["id"], "create_label", name="ops")
        updated = self.call_as(self.admin["id"], "update_label", label=label["id"], name="platform")
        self.assertEqual(updated["name"], "platform")
        deleted = self.call_as(self.admin["id"], "delete_label", label=updated["id"])
        self.assertTrue(deleted["deleted"])

        rotated = self.call_as(self.admin["id"], "rotate_actor_token", actor="member-agent")
        self.assertIn("token", rotated)
        self.assertEqual(
            self.call_as(self.admin["id"], "set_actor_role", actor="viewer-agent", role="member")["role"],
            "member",
        )

    # -- argument validation (experiment 4, defects 1.1 and 1.2) ------------------

    def rpc_error(self, tool_name, arguments):
        response = self.rpc(self.member["id"], "tools/call",
                            {"name": tool_name, "arguments": arguments})
        result = response["result"]
        self.assertTrue(result.get("isError"), result)
        return result["structuredContent"]["error"]

    def test_wrong_field_name_is_invalid_request_not_not_found(self):
        error = self.rpc_error("get_issue", {"id": "APP-1"})
        self.assertEqual(error["code"], "invalid_request")
        self.assertIn("unknown field 'id'", error["message"])
        self.assertIn("issue", error["message"])

    def test_unknown_extra_field_is_rejected(self):
        self.call("create_issue", title="one")
        error = self.rpc_error("get_issue", {"issue": "APP-1", "bogus": 1})
        self.assertEqual(error["code"], "invalid_request")
        self.assertIn("bogus", error["message"])

    def test_close_typo_gets_a_suggestion(self):
        error = self.rpc_error("update_issue",
                               {"issue": "APP-1", "expected_revision": 1, "asignee": "member-agent"})
        self.assertIn("did you mean 'assignee'", error["message"])

    def test_missing_required_field_names_the_field(self):
        error = self.rpc_error("claim_issue", {"issue": "APP-1"})
        self.assertEqual(error["code"], "invalid_request")
        self.assertIn("missing required field 'expected_revision'", error["message"])

    def test_wrong_type_is_invalid_request(self):
        error = self.rpc_error("update_issue", {"issue": "APP-1", "expected_revision": "1"})
        self.assertEqual(error["code"], "invalid_request")
        self.assertIn("expected_revision", error["message"])

    def test_missing_issue_is_still_not_found(self):
        error = self.rpc_error("get_issue", {"issue": "APP-99"})
        self.assertEqual(error["code"], "not_found")

    # -- compact mutation responses (experiment 4, defect 1.3) --------------------

    COMPACT_KEYS = {"identifier", "revision", "status", "category", "blocked", "assignee",
                    "claim_expires_at"}

    def test_mutations_return_compact_confirmation(self):
        created = self.call("create_issue", title="compact")
        updated = self.call("update_issue", issue=created["identifier"],
                            expected_revision=created["revision"], priority="high")
        self.assertEqual(set(updated), self.COMPACT_KEYS)
        self.assertEqual(updated["revision"], created["revision"] + 1)

    def test_return_full_issue_flag_returns_everything(self):
        created = self.call("create_issue", title="full")
        self.call("add_comment", issue=created["identifier"], body="context")
        full = self.call("update_issue", issue=created["identifier"],
                         expected_revision=created["revision"], priority="low",
                         return_full_issue=True)
        self.assertIn("comments", full)
        self.assertEqual(len(full["comments"]), 1)

    def test_add_comment_reports_current_issue_revision(self):
        created = self.call("create_issue", title="chatty")
        comment = self.call("add_comment", issue=created["identifier"], body="note")
        self.assertEqual(comment["issue_revision"], created["revision"])

    # -- claim with status (experiment 4, defect 1.6) -----------------------------

    def test_claim_with_status_is_one_atomic_call(self):
        created = self.call("create_issue", title="start me")
        claimed = self.call("claim_issue", issue=created["identifier"],
                            expected_revision=created["revision"], status="In Progress")
        self.assertEqual(claimed["status"], "In Progress")
        self.assertEqual(claimed["assignee"], "member-agent")
        entries = self.board.activity("issue", self.board.resolve_issue(created["identifier"]))
        claim_entries = [entry for entry in entries if entry["action"] == "claimed"]
        self.assertEqual(len(claim_entries), 1)
        self.assertEqual(claim_entries[0]["data"]["status"]["to"], "In Progress")

    def test_failed_claim_does_not_move_status(self):
        created = self.call("create_issue", title="contested")
        error = self.rpc_error("claim_issue", {"issue": created["identifier"],
                                               "expected_revision": 99, "status": "In Progress"})
        self.assertEqual(error["code"], "conflict")
        issue = self.call("get_issue", issue=created["identifier"])
        self.assertEqual(issue["status"], "Backlog")

    # -- lease extinguished on completion (experiment 4, item 2.5) ----------------

    def test_completion_extinguishes_lease_and_keeps_assignee(self):
        created = self.call("create_issue", title="finish me")
        claimed = self.call("claim_issue", issue=created["identifier"],
                            expected_revision=created["revision"], status="In Progress")
        self.assertIsNotNone(claimed["claim_expires_at"])
        done = self.call("update_issue", issue=created["identifier"],
                         expected_revision=claimed["revision"], status="Done")
        self.assertIsNone(done["claim_expires_at"])
        self.assertEqual(done["assignee"], "member-agent")

    # -- activity identifiers (experiment 4, defect 1.7) --------------------------

    def test_activity_entries_carry_issue_identifier(self):
        created = self.call("create_issue", title="logged")
        entries = self.board.activity("issue")
        self.assertEqual(entries[0]["identifier"], created["identifier"])


if __name__ == "__main__":
    unittest.main()
