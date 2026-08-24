import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_board.db import AuthorizationError, Board, ConflictError


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.db"
        self.board = Board(self.path)
        self.board.init()
        self.board.configure_board("APP", "App")
        self.admin = self.board.create_actor("admin-one")

    def tearDown(self):
        self.tmp.cleanup()

    # -- issue creation defaults ------------------------------------------------

    def test_create_issue_defaults(self):
        issue = self.board.create_issue(self.admin["id"], "First issue")
        self.assertEqual(issue["status"], "Backlog")
        self.assertEqual(issue["identifier"], "APP-1")
        self.assertEqual(issue["priority"], "medium")

    def test_resolve_issue_is_case_insensitive_and_checks_prefix(self):
        issue = self.board.create_issue(self.admin["id"], "Resolve me")
        self.assertEqual(self.board.resolve_issue("APP-1"), issue["id"])
        self.assertEqual(self.board.resolve_issue("app-1"), issue["id"])
        with self.assertRaises(KeyError):
            self.board.resolve_issue("WRONG-1")

    # -- transitions and start policy --------------------------------------------

    def test_update_issue_allows_free_transition_to_any_status(self):
        issue = self.board.create_issue(self.admin["id"], "Free transitions")
        done = self.board.update_issue(self.admin["id"], issue["id"], status="Done")
        self.assertEqual(done["status"], "Done")
        canceled = self.board.update_issue(self.admin["id"], issue["id"], status="Canceled")
        self.assertEqual(canceled["status"], "Canceled")

    def test_starting_without_assignee_is_rejected_by_policy(self):
        issue = self.board.create_issue(self.admin["id"], "Needs a claim")
        with self.assertRaisesRegex(ValueError, "claimed or assigned"):
            self.board.update_issue(self.admin["id"], issue["id"], status="In Progress")

    def test_policy_can_relax_the_assignee_requirement(self):
        self.board.configure_board("APP", "App", agent_policy={"require_assignee_before_start": False})
        issue = self.board.create_issue(self.admin["id"], "No claim required")
        started = self.board.update_issue(self.admin["id"], issue["id"], status="In Progress")
        self.assertEqual(started["status"], "In Progress")

    # -- claims -------------------------------------------------------------------

    def test_claim_is_atomic_and_can_be_released(self):
        issue = self.board.create_issue(self.admin["id"], "Claimable")
        other = self.board.create_actor("agent-two")
        claimed = self.board.claim_issue(self.admin["id"], issue["id"], issue["revision"])
        self.assertEqual(claimed["assignee_id"], self.admin["id"])
        self.assertIsNotNone(claimed["claim_expires_at"])
        with self.assertRaises(ConflictError):
            self.board.claim_issue(other["id"], issue["id"], issue["revision"])
        released = self.board.release_issue(self.admin["id"], issue["id"], claimed["revision"])
        self.assertIsNone(released["assignee_id"])
        self.assertIsNone(released["claim_expires_at"])

    def test_stale_expected_revision_is_a_conflict(self):
        issue = self.board.create_issue(self.admin["id"], "Versioned")
        self.board.update_issue(self.admin["id"], issue["id"], title="First edit")
        with self.assertRaises(ConflictError):
            self.board.update_issue(
                self.admin["id"], issue["id"], expected_revision=issue["revision"], title="Stale edit"
            )

    # -- labels ---------------------------------------------------------------------

    def test_labels_can_be_set_on_create_and_update(self):
        self.board.create_label(self.admin["id"], "backend")
        self.board.create_label(self.admin["id"], "urgent-fix")
        issue = self.board.create_issue(self.admin["id"], "Labeled", labels=["backend"])
        self.assertEqual([label["name"] for label in issue["labels"]], ["backend"])
        updated = self.board.update_issue(self.admin["id"], issue["id"], labels=["backend", "urgent-fix"])
        self.assertEqual(sorted(label["name"] for label in updated["labels"]), ["backend", "urgent-fix"])

    # -- parent/child hierarchy -------------------------------------------------------

    def test_parent_assignment_rejects_cycles_and_lists_children(self):
        parent = self.board.create_issue(self.admin["id"], "Parent")
        child = self.board.create_issue(self.admin["id"], "Child")
        self.board.update_issue(self.admin["id"], child["id"], parent_id=parent["id"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.board.update_issue(self.admin["id"], parent["id"], parent_id=child["id"])
        refreshed_parent = self.board.get_issue(parent["id"])
        self.assertEqual([item["id"] for item in refreshed_parent["children"]], [child["id"]])

    def test_parent_cannot_be_itself(self):
        issue = self.board.create_issue(self.admin["id"], "Self parent")
        with self.assertRaisesRegex(ValueError, "own parent"):
            self.board.update_issue(self.admin["id"], issue["id"], parent_id=issue["id"])

    # -- dependencies and the blocked flag ---------------------------------------------

    def test_dependency_cycles_are_rejected_direct_and_transitive(self):
        a = self.board.create_issue(self.admin["id"], "A")
        b = self.board.create_issue(self.admin["id"], "B")
        c = self.board.create_issue(self.admin["id"], "C")
        self.board.add_dependency(self.admin["id"], a["id"], b["id"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.board.add_dependency(self.admin["id"], b["id"], a["id"])
        self.board.add_dependency(self.admin["id"], b["id"], c["id"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.board.add_dependency(self.admin["id"], c["id"], a["id"])

    def test_blocked_flag_follows_blocker_category_including_reopen(self):
        blocked_issue = self.board.create_issue(self.admin["id"], "Blocked issue")
        blocker = self.board.create_issue(self.admin["id"], "Blocker")
        self.board.add_dependency(self.admin["id"], blocked_issue["id"], blocker["id"])
        self.assertTrue(self.board.get_issue(blocked_issue["id"])["blocked"])
        self.board.update_issue(self.admin["id"], blocker["id"], status="Done")
        self.assertFalse(self.board.get_issue(blocked_issue["id"])["blocked"])
        self.board.update_issue(self.admin["id"], blocker["id"], status="Todo")
        self.assertTrue(self.board.get_issue(blocked_issue["id"])["blocked"])

    # -- comments and authorization -----------------------------------------------------

    def test_comment_authorship_is_enforced(self):
        issue = self.board.create_issue(self.admin["id"], "Discussed")
        author = self.board.create_actor("author-agent")
        other_member = self.board.create_actor("other-agent")
        comment = self.board.add_comment(author["id"], issue["id"], "Initial note")
        with self.assertRaises(AuthorizationError):
            self.board.update_comment(other_member["id"], comment["id"], "Hijacked")
        with self.assertRaises(AuthorizationError):
            self.board.delete_comment(other_member["id"], comment["id"])
        updated = self.board.update_comment(author["id"], comment["id"], "Edited by author")
        self.assertEqual(updated["body"], "Edited by author")
        by_admin = self.board.update_comment(self.admin["id"], comment["id"], "Edited by admin")
        self.assertEqual(by_admin["body"], "Edited by admin")
        deleted = self.board.delete_comment(self.admin["id"], comment["id"])
        self.assertTrue(deleted["deleted"])

    # -- git links -------------------------------------------------------------------------

    def test_git_links_are_unique_by_kind_and_ref(self):
        issue = self.board.create_issue(self.admin["id"], "Linked")
        self.board.add_git_link(self.admin["id"], issue["id"], "abc1234", kind="commit")
        self.board.add_git_link(self.admin["id"], issue["id"], "abc1234", kind="commit")
        refreshed = self.board.get_issue(issue["id"])
        self.assertEqual(len(refreshed["git_links"]), 1)

    # -- milestones ------------------------------------------------------------------------

    def test_milestone_assignment(self):
        milestone = self.board.create_milestone(self.admin["id"], "August release")
        issue = self.board.create_issue(self.admin["id"], "Milestone issue", milestone_id=milestone["id"])
        self.assertEqual(issue["milestone_id"], milestone["id"])
        another = self.board.create_issue(self.admin["id"], "Assigned later")
        assigned = self.board.update_issue(self.admin["id"], another["id"], milestone_id=milestone["id"])
        self.assertEqual(assigned["milestone_id"], milestone["id"])

    # -- activity --------------------------------------------------------------------------

    def test_activity_is_append_only(self):
        issue = self.board.create_issue(self.admin["id"], "Audited")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            with self.board.connect() as db:
                event = db.execute(
                    "SELECT id FROM activity WHERE entity_type='issue' AND entity_id=? ORDER BY id DESC LIMIT 1",
                    (issue["id"],),
                ).fetchone()
                db.execute("UPDATE activity SET action='tampered' WHERE id=?", (event["id"],))

    def test_activity_data_is_trimmed_to_field_names(self):
        issue = self.board.create_issue(self.admin["id"], "Trimmed", description="a" * 500)
        self.board.update_issue(self.admin["id"], issue["id"], description="b" * 500, title="New title")
        events = self.board.activity("issue", issue["id"])
        updated_event = next(event for event in events if event["action"] == "updated")
        self.assertEqual(sorted(updated_event["data"]["fields"]), ["description", "title"])
        self.assertNotIn("description", updated_event["data"])
        serialized = str(updated_event["data"])
        self.assertNotIn("a" * 500, serialized)
        self.assertNotIn("b" * 500, serialized)


if __name__ == "__main__":
    unittest.main()
