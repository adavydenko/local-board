import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board


class BoardValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.actor = self.board.create_actor("validation-agent")
        self.project = self.board.create_project(self.actor["id"], "VAL", "Validation")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_entities_and_invalid_types(self):
        for operation in (
            lambda: self.board.get_actor(999),
            lambda: self.board.get_project(999),
            lambda: self.board.get_issue(999),
            lambda: self.board.resolve_project("UNKNOWN"),
            lambda: self.board.resolve_issue("VAL-999"),
        ):
            with self.subTest(operation=operation), self.assertRaises(KeyError):
                operation()
        with self.assertRaises((AttributeError, TypeError)):
            self.board.create_project(self.actor["id"], 123, "Bad key")
        with self.assertRaises(ValueError):
            self.board.set_workflow(self.actor["id"], self.project["id"], "task", ["todo"], [["todo"]])

    def test_duplicate_keys_and_related_items(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.board.create_project(self.actor["id"], "VAL", "Duplicate")
        self.board.create_milestone(self.actor["id"], self.project["id"], "v1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.board.create_milestone(self.actor["id"], self.project["id"], "v1")
        label = self.board.create_label(self.actor["id"], self.project["id"], "bug")
        with self.assertRaises(sqlite3.IntegrityError):
            self.board.create_label(self.actor["id"], self.project["id"], "bug")
        issue = self.board.create_issue(self.actor["id"], self.project["id"], "One")
        self.board.add_label(self.actor["id"], issue["id"], label["id"])
        self.board.add_label(self.actor["id"], issue["id"], label["id"])
        self.assertEqual(len(self.board.get_issue(issue["id"])["labels"]), 1)

    def test_priorities_workflow_and_self_dependency(self):
        for priority in ("none", "low", "medium", "high", "urgent"):
            issue = self.board.create_issue(self.actor["id"], self.project["id"], priority, priority=priority)
            self.assertEqual(issue["priority"], priority)
        with self.assertRaisesRegex(ValueError, "invalid issue type or priority"):
            self.board.create_issue(self.actor["id"], self.project["id"], "Bad", priority="critical")
        with self.assertRaisesRegex(ValueError, "invalid priority"):
            self.board.update_issue(self.actor["id"], 1, priority="critical")
        with self.assertRaisesRegex(ValueError, "invalid workflow"):
            self.board.set_workflow(self.actor["id"], self.project["id"], "task", ["todo"], [["todo", "done"]])
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.board.transition_issue(self.actor["id"], 1, "done")
        with self.assertRaisesRegex(ValueError, "same project"):
            self.board.add_related(self.actor["id"], 1, "dependency", depends_on_id=1)

    def test_attachments_git_links_and_activity_authorship(self):
        issue = self.board.create_issue(self.actor["id"], self.project["id"], "Relations")
        attachment = self.board.add_related(self.actor["id"], issue["id"], "attachment", name="spec", path="docs/spec.md")
        link = self.board.add_related(self.actor["id"], issue["id"], "git_link", link_kind="commit", ref="abc123")
        context = self.board.get_issue_context(issue["id"])
        self.assertEqual(context["attachments"][0]["id"], attachment["id"])
        self.assertEqual(context["git_links"][0]["id"], link["id"])
        self.board.delete_attachment(self.actor["id"], attachment["id"])
        self.board.delete_git_link(self.actor["id"], link["id"])
        actions = {event["action"]: event["actor"] for event in self.board.activity("issue", issue["id"])}
        self.assertEqual(actions["attachment_added"], self.actor["name"])
        self.assertEqual(actions["git_link_added"], self.actor["name"])
        self.assertEqual(actions["attachment_deleted"], self.actor["name"])
        self.assertEqual(actions["git_link_deleted"], self.actor["name"])


if __name__ == "__main__":
    unittest.main()
