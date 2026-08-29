"""Unit tests for reset.py: the inverse of init, planned and applied on tmp trees."""

import json
import os
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path

from local_board import reset
from local_board.onboarding import TEMPLATES


def _scaffold(root: Path) -> tuple[Path, Path]:
    """A minimal repo shaped the way init leaves it."""
    state = root / ".local-board" / "state"
    state.mkdir(parents=True)
    (state / "board.db").write_text("db", encoding="utf-8")
    config = root / ".local-board" / "project.toml"
    config.write_text("schema_version = 2\n", encoding="utf-8")
    for relative in TEMPLATES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")
    bridge = files("local_board").joinpath("templates", "local-board-agents-bridge.md")
    (root / "AGENTS.md").write_text(bridge.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".gitignore").write_text("*.pyc" + reset.GITIGNORE_BLOCK, encoding="utf-8")
    return state, config


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state, self.config = _scaffold(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    # -- server_pid ---------------------------------------------------------------

    def test_server_pid_without_discovery_file_is_none(self):
        self.assertIsNone(reset.server_pid(self.state))

    def test_server_pid_of_dead_process_is_none(self):
        (self.state / "server.json").write_text(json.dumps({"pid": 2 ** 22 + 12345}))
        self.assertIsNone(reset.server_pid(self.state))

    def test_server_pid_of_live_process_is_reported(self):
        (self.state / "server.json").write_text(json.dumps({"pid": os.getpid()}))
        self.assertEqual(reset.server_pid(self.state), os.getpid())

    def test_server_pid_with_garbage_discovery_is_none(self):
        (self.state / "server.json").write_text("not json")
        self.assertIsNone(reset.server_pid(self.state))

    # -- plan ---------------------------------------------------------------------

    def test_state_only_plan_moves_state_aside(self):
        removals = reset.plan(self.root, self.state, self.config, everything=False, purge=False)
        self.assertEqual([(r.kind, r.action) for r in removals], [("state", "move")])

    def test_purge_turns_moves_into_deletes(self):
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=True)
        actions = {r.kind: r.action for r in removals}
        self.assertEqual(actions["state"], "delete")

    def test_everything_plan_covers_config_onboarding_and_gitignore(self):
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=False)
        kinds = {r.kind for r in removals}
        self.assertEqual(kinds, {"state", "config", "onboarding", "gitignore"})
        gitignore = [r for r in removals if r.kind == "gitignore"][0]
        self.assertEqual(gitignore.action, "edit")

    def test_edited_agents_bridge_is_kept_not_deleted(self):
        (self.root / "AGENTS.md").write_text("my own policy\n", encoding="utf-8")
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=False)
        bridge = [r for r in removals if r.path == self.root / "AGENTS.md"][0]
        self.assertEqual(bridge.action, "keep")

    def test_missing_pieces_are_simply_absent_from_the_plan(self):
        (self.root / ".gitignore").unlink()
        self.config.unlink()
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=False)
        kinds = [r.kind for r in removals]
        self.assertNotIn("gitignore", kinds)
        self.assertNotIn("config", kinds)

    # -- apply --------------------------------------------------------------------

    def test_apply_moves_state_beside_the_original(self):
        removals = reset.plan(self.root, self.state, self.config, everything=False, purge=False)
        done = reset.apply(removals, stamp="TEST")
        self.assertFalse(self.state.exists())
        moved = self.state.with_name("state.removed-TEST")
        self.assertTrue(moved.is_dir())
        self.assertEqual(done[0]["action"], "moved")
        self.assertEqual(done[0]["destination"], str(moved))

    def test_apply_with_purge_deletes_and_prunes_empty_scaffolding(self):
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=True)
        done = reset.apply(removals, stamp="TEST")
        self.assertFalse((self.root / ".local-board").exists())
        self.assertFalse((self.root / ".agents").exists())
        self.assertTrue(self.root.exists())  # never the repo root itself
        self.assertIn({"path": str(self.config), "action": "deleted"}, done)

    def test_apply_reports_kept_entries_without_touching_them(self):
        (self.root / "AGENTS.md").write_text("my own policy\n", encoding="utf-8")
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=True)
        done = reset.apply(removals, stamp="TEST")
        self.assertTrue((self.root / "AGENTS.md").exists())
        kept = [entry for entry in done if entry["action"] == "kept"]
        self.assertEqual(len(kept), 1)

    def test_gitignore_block_is_stripped_exactly(self):
        removals = reset.plan(self.root, self.state, self.config, everything=True, purge=False)
        reset.apply(removals, stamp="TEST")
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), "*.pyc\n")

    def test_hand_edited_gitignore_block_is_stripped_line_by_line(self):
        (self.root / ".gitignore").write_text(
            "*.pyc\n\n# Local Board runtime\n.local-board/state/\n.local-board/backups/\nnode_modules/\n",
            encoding="utf-8",
        )
        reset._strip_gitignore_block(self.root / ".gitignore")
        content = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("Local Board", content)
        self.assertNotIn(".local-board/", content)
        self.assertIn("*.pyc", content)
        self.assertIn("node_modules/", content)


if __name__ == "__main__":
    unittest.main()
