import tempfile
import unittest
from pathlib import Path

from local_board.config import ConfigError, ConfigService, default_config, load_config
from local_board.db import Board, ISSUE_TYPES


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.path = root / "project.toml"
        self.path.write_text(default_config("Application", "APP"), encoding="utf-8")
        self.board = Board(root / "board.db"); self.board.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_config_defines_every_issue_type(self):
        config = load_config(self.path)
        self.assertEqual(set(config.data["workflows"]), set(ISSUE_TYPES))

    def test_plan_apply_and_idempotent_reapply(self):
        config = load_config(self.path); service = ConfigService(self.board)
        self.assertTrue(service.plan(config)["changed"])
        applied = service.apply(config)
        self.assertTrue(applied["applied"])
        plan = service.plan(config)
        self.assertFalse(plan["changed"])
        self.assertEqual(plan["actions"], [])
        self.assertFalse(service.apply(config)["applied"])
        project = self.board.list_projects()[0]
        self.assertEqual(project["key"], "APP")
        actor = self.board.create_actor("defaults-agent")
        issue = self.board.create_issue(actor["id"], project["id"], "Uses config defaults")
        self.assertEqual((issue["type"], issue["priority"]), ("task", "medium"))
        with self.board.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflows WHERE project_id=? AND managed_by='config'", (project["id"],)).fetchone()[0], len(ISSUE_TYPES))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM config_applies").fetchone()[0], 1)

    def test_apply_updates_config_managed_label(self):
        service = ConfigService(self.board); service.apply(load_config(self.path))
        content = self.path.read_text().replace('name = "Backend"', 'name = "Platform"')
        self.path.write_text(content)
        result = service.apply(load_config(self.path))
        self.assertIn({"action": "update", "entity": "label", "key": "backend"}, result["actions"])
        with self.board.connect() as db:
            self.assertEqual(db.execute("SELECT name FROM labels WHERE key='backend'").fetchone()[0], "Platform")

    def test_mcp_style_workflow_mutation_cannot_override_config(self):
        ConfigService(self.board).apply(load_config(self.path))
        actor = self.board.create_actor("agent")
        with self.assertRaisesRegex(ValueError, "managed by"):
            self.board.set_workflow(actor["id"], 1, "task", ["todo", "done"], [["todo", "done"]])

    def test_agent_policy_requires_claim_before_in_progress(self):
        ConfigService(self.board).apply(load_config(self.path))
        actor = self.board.create_actor("policy-agent")
        issue = self.board.create_issue(actor["id"], 1, "Policy guarded")
        issue = self.board.transition_issue(actor["id"], issue["id"], "todo", issue["revision"])
        with self.assertRaisesRegex(ValueError, "claimed or assigned"):
            self.board.transition_issue(actor["id"], issue["id"], "in_progress", issue["revision"])
        issue = self.board.claim_issue(actor["id"], issue["id"], issue["revision"])
        issue = self.board.transition_issue(actor["id"], issue["id"], "in_progress", issue["revision"])
        self.assertEqual(issue["status"], "in_progress")

    def test_validation_rejects_unreachable_state(self):
        self.path.write_text(self.path.read_text().replace('states = ["backlog",', 'states = ["orphan", "backlog",', 1))
        with self.assertRaisesRegex(ConfigError, "unreachable states"):
            load_config(self.path)

    def test_apply_rejects_removing_state_used_by_issue(self):
        service = ConfigService(self.board); service.apply(load_config(self.path))
        actor = self.board.create_actor("agent")
        issue = self.board.create_issue(actor["id"], 1, "Active work")
        self.board.transition_issue(actor["id"], issue["id"], "todo")
        content = self.path.read_text()
        task_start = content.index("[workflows.task]")
        next_workflow = content.index("[workflows.bug]", task_start)
        task = content[task_start:next_workflow]
        task = task.replace('"backlog", "todo", "in_progress"', '"backlog", "in_progress"')
        task = task.replace('["backlog", "todo"],', '["backlog", "in_progress"],')
        task = task.replace('  ["todo", "in_progress"],\n', "")
        task = task.replace('  ["todo", "cancelled"],\n', "")
        self.path.write_text(content[:task_start] + task + content[next_workflow:])
        with self.assertRaisesRegex(ConfigError, "states used by issues"):
            service.apply(load_config(self.path))
