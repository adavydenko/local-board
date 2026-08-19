import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board
from local_board.repository import Repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CliIntegrationTest(unittest.TestCase):
    def test_init_actor_and_sync_branch_in_git_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sample"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo, check=True)
            env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
            subprocess.run([sys.executable, "-m", "local_board.cli", "init"], cwd=repo, env=env, check=True, capture_output=True, text=True)
            actor_result = subprocess.run(
                [sys.executable, "-m", "local_board.cli", "actor", "branch-agent"],
                cwd=repo, env=env, check=True, capture_output=True, text=True,
            )
            token = actor_result.stdout.split("Token (shown once): ", 1)[1].strip()
            board = Board(Repository.discover(repo).database_path)
            actor = board.authenticate(token)
            project = board.list_projects()[0]
            issue = board.create_issue(actor["id"], project["id"], "Branch work")
            subprocess.run(["git", "checkout", "-qb", f"feature/{issue['identifier']}-sync"], cwd=repo, check=True)
            synced = subprocess.run(
                [sys.executable, "-m", "local_board.cli", "sync-branch", "--token", token],
                cwd=repo, env=env, check=True, capture_output=True, text=True,
            )
            self.assertIn("1 issue(s)", synced.stdout)
            self.assertEqual(board.get_issue_context(issue["id"])["git_links"][0]["ref"], f"feature/{issue['identifier']}-sync")
            rejected = subprocess.run(
                [sys.executable, "-m", "local_board.cli", "sync-branch", "--token", "invalid"],
                cwd=repo, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("valid --token", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
