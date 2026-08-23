import subprocess
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board
from local_board.repository import Repository


def run(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


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

    def test_linked_worktree_shares_primary_database(self):
        linked = Path(self.tmp.name) / "linked"
        run("git", "worktree", "add", "-q", "-b", "linked-test", str(linked), cwd=self.root)
        primary = Repository.discover(self.root)
        secondary = Repository.discover(linked)
        self.assertEqual(primary.git_common_dir, secondary.git_common_dir)
        self.assertEqual(primary.database_path, secondary.database_path)
        self.assertEqual(primary.database_path, self.root / ".local-board" / "state" / "board.db")

        # Prove real sharing, not merely equal paths: a write through the primary
        # checkout must be visible through the linked worktree's own Board handle.
        board = Board(primary.database_path)
        board.init()
        board.configure_board("APP", "App")
        actor = board.create_actor("from-primary")
        seen_from_worktree = Board(secondary.database_path).get_actor(actor["id"])
        self.assertEqual(seen_from_worktree["name"], "from-primary")


if __name__ == "__main__":
    unittest.main()
