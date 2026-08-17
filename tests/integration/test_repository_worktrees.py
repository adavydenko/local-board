import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_board.repository import Repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True)


class WorktreeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        run("git", "init", "-q", cwd=self.root)
        run("git", "config", "user.email", "tests@example.invalid", cwd=self.root)
        run("git", "config", "user.name", "Local Board Tests", cwd=self.root)
        (self.root / "README.md").write_text("test\n")
        run("git", "add", "README.md", cwd=self.root)
        run("git", "commit", "-qm", "initial", cwd=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nested_directory_discovers_same_database(self):
        nested = self.root / "src" / "deep"
        nested.mkdir(parents=True)
        self.assertEqual(Repository.discover(self.root).database_path, Repository.discover(nested).database_path)

    def test_linked_worktree_has_checkout_local_runtime_path(self):
        linked = Path(self.tmp.name) / "linked"
        run("git", "worktree", "add", "-q", "-b", "linked-test", str(linked), cwd=self.root)
        primary = Repository.discover(self.root)
        secondary = Repository.discover(linked)
        self.assertEqual(primary.git_common_dir, secondary.git_common_dir)
        self.assertEqual(primary.database_path, self.root / ".local-board" / "state" / "board.db")
        self.assertEqual(secondary.database_path, linked / ".local-board" / "state" / "board.db")
        self.assertNotEqual(primary.database_path, secondary.database_path)

    def test_cli_status_reports_repository_database(self):
        nested = self.root / "nested"
        nested.mkdir()
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        result = run(sys.executable, "-m", "local_board.cli", "status", "--json", cwd=nested, env=env)
        status = json.loads(result.stdout)
        self.assertEqual(Path(status["repository"]), self.root.resolve())
        self.assertEqual(Path(status["database"]), Repository.discover(self.root).database_path)
        self.assertEqual(status["schema_version"], 2)

    def test_init_creates_tracked_config_and_config_cli_is_idempotent(self):
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        run(sys.executable, "-m", "local_board.cli", "init", cwd=self.root, env=env)
        config = self.root / ".local-board" / "project.toml"
        self.assertTrue(config.exists())
        self.assertIn(".local-board/state/", (self.root / ".gitignore").read_text())
        validated = run(sys.executable, "-m", "local_board.cli", "config", "validate", cwd=self.root, env=env)
        self.assertIn("Valid:", validated.stdout)
        planned = run(sys.executable, "-m", "local_board.cli", "config", "plan", cwd=self.root, env=env)
        self.assertFalse(json.loads(planned.stdout)["changed"])
