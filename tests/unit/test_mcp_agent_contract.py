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

        comment = self.call("add_comment", issue="APP-1", body="Working on it",
                            return_full_comment=True)
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

    # -- list_issues filter resolution --------------------------------------------

    def test_list_issues_resolves_reference_filters(self):
        milestone = self.call("create_milestone", name="Phase one", key="p1")
        self.call("create_label", name="Backend", key="backend")
        parent = self.call("create_issue", title="epic")
        self.call("create_issue", title="child work item", milestone="p1",
                  assignee="member-agent", labels=["backend"], parent=parent["identifier"])
        self.call("create_issue", title="unrelated")

        by_milestone = self.call("list_issues", milestone="p1")
        self.assertEqual([issue["title"] for issue in by_milestone], ["child work item"])
        self.assertEqual(by_milestone[0]["milestone_id"], milestone["id"])

        by_assignee = self.call("list_issues", assignee="member-agent")
        self.assertEqual([issue["title"] for issue in by_assignee], ["child work item"])

        by_parent = self.call("list_issues", parent=parent["identifier"])
        self.assertEqual([issue["title"] for issue in by_parent], ["child work item"])

        combined = self.call("list_issues", label="backend", query="child")
        self.assertEqual([issue["title"] for issue in combined], ["child work item"])

    # -- update_issue null clearing ------------------------------------------------

    def test_update_issue_null_clears_references(self):
        self.call("create_milestone", name="Phase two", key="p2")
        parent = self.call("create_issue", title="parent")
        created = self.call("create_issue", title="clearable", milestone="p2",
                            assignee="member-agent", parent=parent["identifier"])
        cleared = self.call("update_issue", issue=created["identifier"],
                            expected_revision=created["revision"],
                            assignee=None, milestone=None, parent=None,
                            return_full_issue=True)
        self.assertIsNone(cleared["assignee_id"])
        self.assertIsNone(cleared["milestone_id"])
        self.assertIsNone(cleared["parent_id"])

    # -- correction tools over MCP (admin) -----------------------------------------

    def test_admin_correction_tools_for_milestones_git_links_comments(self):
        milestone = self.call_as(self.admin["id"], "create_milestone", name="Draft", key="d1")
        renamed = self.call_as(self.admin["id"], "update_milestone", milestone="d1", name="Final")
        self.assertEqual(renamed["name"], "Final")

        created = self.call("create_issue", title="linked")
        self.call("add_git_link", issue=created["identifier"], ref="feature/x")
        link = self.call("get_issue", issue=created["identifier"])["git_links"][0]
        updated = self.call_as(self.admin["id"], "update_git_link",
                               link_id=link["id"], ref="feature/y", kind="pr")
        self.assertEqual((updated["ref"], updated["kind"]), ("feature/y", "pr"))
        deleted = self.call_as(self.admin["id"], "delete_git_link", link_id=link["id"])
        self.assertTrue(deleted["deleted"])

        comment = self.call("add_comment", issue=created["identifier"], body="typo")
        removed = self.call_as(self.admin["id"], "delete_comment", comment_id=comment["id"])
        self.assertTrue(removed["deleted"])

        gone = self.call_as(self.admin["id"], "delete_milestone", milestone="d1")
        self.assertTrue(gone["deleted"])


class ValidatorFuzzTest(unittest.TestCase):
    """The hand-rolled schema validator must never let junk arguments escape as a raw
    exception: every response is a structured result or a structured error."""

    SEED = 20260824
    ROUNDS_PER_TOOL = 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.admin = self.board.create_actor("fuzz-admin")
        self.board.create_issue(self.admin["id"], "seed issue")

    def tearDown(self):
        self.tmp.cleanup()

    def _junk_value(self, rng):
        choices = [
            None, True, False, 0, -1, 99999, 1.5, "", "APP-1", "APP-999", "id", "bogus",
            "1", [], [1, "x"], ["APP-1"], {"nested": 1}, "In Progress", "no-such-status",
            rng.choice(["a", "z"]) * rng.randint(1, 30),
        ]
        return rng.choice(choices)

    def test_random_arguments_always_yield_structured_responses(self):
        import random

        from local_board.mcp import schemas

        rng = random.Random(self.SEED)
        field_pool = ["issue", "id", "expected_revision", "status", "assignee", "milestone",
                      "labels", "body", "comment_id", "link_id", "label", "name", "kind",
                      "role", "actor", "depends_on", "ref", "url", "title", "bogus", "query"]
        for entry in schemas("admin"):
            for _ in range(self.ROUNDS_PER_TOOL):
                arguments = {
                    rng.choice(field_pool): self._junk_value(rng)
                    for _ in range(rng.randint(0, 4))
                }
                response = handle(self.board, self.admin["id"], {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": entry["name"], "arguments": arguments},
                })
                result = response["result"]
                if result.get("isError"):
                    error = result["structuredContent"]["error"]
                    self.assertIn(error["code"],
                                  {"invalid_request", "not_found", "conflict", "blocked",
                                   "unauthorized", "internal"},
                                  (entry["name"], arguments, error))
                else:
                    self.assertIn("structuredContent", result)


if __name__ == "__main__":
    unittest.main()


class Experiment5FixesTest(unittest.TestCase):
    """Regressions for the second experiment report (compact comments, link
    confirmations, comment windows, list summaries, conflict messages, leases)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.worker = self.board.create_actor("worker")
        self.reviewer = self.board.create_actor("reviewer")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, actor, tool_name, **arguments):
        return call_tool(self.board, actor["id"], tool_name, arguments)

    def test_add_comment_is_compact_by_default(self):
        created = self.call(self.worker, "create_issue", title="talkative")
        compact = self.call(self.worker, "add_comment", issue="APP-1", body="a long body " * 50)
        self.assertEqual(set(compact), {"id", "issue_id", "issue_revision", "created_at"})
        self.assertEqual(compact["issue_revision"], created["revision"])
        full = self.call(self.worker, "add_comment", issue="APP-1", body="short",
                         return_full_comment=True)
        self.assertEqual(full["body"], "short")

    def test_add_git_link_returns_the_created_link(self):
        self.call(self.worker, "create_issue", title="linked")
        link = self.call(self.worker, "add_git_link", issue="APP-1", ref="feature/x")
        self.assertEqual((link["kind"], link["ref"], link["issue"]), ("branch", "feature/x", "APP-1"))
        self.assertIn("id", link)
        duplicate = self.call(self.worker, "add_git_link", issue="APP-1", ref="feature/x")
        self.assertEqual(duplicate["id"], link["id"])

    def test_add_git_link_batch_refs(self):
        self.call(self.worker, "create_issue", title="batched")
        result = self.call(self.worker, "add_git_link", issue="APP-1",
                           refs=["abc123", "def456", "abc123"], kind="commit")
        self.assertEqual(result["issue"], "APP-1")
        self.assertEqual([link["ref"] for link in result["links"]], ["abc123", "def456", "abc123"])
        self.assertEqual(result["links"][0]["id"], result["links"][2]["id"])
        with self.assertRaises(ValueError):
            self.call(self.worker, "add_git_link", issue="APP-1", ref="x", refs=["y"])
        with self.assertRaises(ValueError):
            self.call(self.worker, "add_git_link", issue="APP-1")

    def test_get_issue_comment_window(self):
        self.call(self.worker, "create_issue", title="threaded")
        for index in range(4):
            self.call(self.worker, "add_comment", issue="APP-1", body=f"comment {index}")
        everything = self.call(self.worker, "get_issue", issue="APP-1")
        self.assertEqual(len(everything["comments"]), 4)
        self.assertEqual(everything["comments_total"], 4)
        none = self.call(self.worker, "get_issue", issue="APP-1", comments="none")
        self.assertEqual(none["comments"], [])
        self.assertEqual(none["comments_total"], 4)
        last = self.call(self.worker, "get_issue", issue="APP-1", comments=2)
        self.assertEqual([item["body"] for item in last["comments"]], ["comment 2", "comment 3"])

    def test_list_issues_carries_labels_and_assignee_name(self):
        self.call(self.worker, "create_label", name="Review required", key="review_required")
        self.call(self.worker, "create_issue", title="owned",
                  assignee="worker", labels=["review_required"])
        summary = self.call(self.worker, "list_issues")[0]
        self.assertEqual(summary["assignee"], "worker")
        self.assertEqual(summary["labels"], ["review_required"])

    def test_conflict_message_names_current_revision(self):
        created = self.call(self.worker, "create_issue", title="conflicted")
        self.call(self.worker, "update_issue", issue="APP-1",
                  expected_revision=created["revision"], title="second")
        with self.assertRaises(Exception) as caught:
            self.call(self.worker, "update_issue", issue="APP-1",
                      expected_revision=99, title="stale")
        self.assertIn("expected 99, current 2", str(caught.exception))

    def test_claim_conflict_names_the_holder(self):
        created = self.call(self.worker, "create_issue", title="held")
        claimed = self.call(self.worker, "claim_issue", issue="APP-1",
                            expected_revision=created["revision"])
        with self.assertRaises(Exception) as caught:
            self.call(self.reviewer, "claim_issue", issue="APP-1",
                      expected_revision=claimed["revision"])
        message = str(caught.exception)
        self.assertIn("held by worker", message)
        self.assertIn(f"current {claimed['revision']}", message)

    def test_claim_with_status_advances_revision_exactly_once(self):
        created = self.call(self.worker, "create_issue", title="single step")
        claimed = self.call(self.worker, "claim_issue", issue="APP-1",
                            expected_revision=created["revision"], status="In Progress")
        self.assertEqual(claimed["revision"], created["revision"] + 1)
        self.assertEqual(claimed["status"], "In Progress")

    def test_revoking_someone_elses_live_lease_is_reported(self):
        created = self.call(self.worker, "create_issue", title="reviewed work")
        claimed = self.call(self.worker, "claim_issue", issue="APP-1",
                            expected_revision=created["revision"], status="In Progress")
        closed = self.call(self.reviewer, "update_issue", issue="APP-1",
                           expected_revision=claimed["revision"], status="Done")
        self.assertEqual(closed["lease_revoked_from"], "worker")
        self.assertEqual(closed["assignee"], "worker")
        entries = self.board.activity("issue", 1)
        self.assertEqual(entries[0]["data"].get("lease_revoked_from"), "worker")

    def test_closing_your_own_claim_reports_no_revocation(self):
        created = self.call(self.worker, "create_issue", title="own work")
        claimed = self.call(self.worker, "claim_issue", issue="APP-1",
                            expected_revision=created["revision"], status="In Progress")
        closed = self.call(self.worker, "update_issue", issue="APP-1",
                           expected_revision=claimed["revision"], status="Done")
        self.assertNotIn("lease_revoked_from", closed)

    def test_list_activity_accepts_issue_identifier(self):
        self.call(self.worker, "create_issue", title="first")
        self.call(self.worker, "create_issue", title="second")
        self.call(self.worker, "add_comment", issue="APP-2", body="note")
        entries = self.call(self.worker, "list_activity", issue="APP-2")
        self.assertTrue(entries)
        self.assertTrue(all(entry["identifier"] == "APP-2" for entry in entries))
