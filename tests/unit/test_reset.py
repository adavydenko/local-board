"""`reset` is the inverse of `init`, and inverses are where data gets lost.

These assert the guardrails rather than the wording: nothing happens without
--force, a live server blocks it, state is recoverable unless purged, and
files Local Board did not write are never removed.
"""

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from local_board.onboarding import TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    return env


def run_cli(*args, cwd):
    return subprocess.run(["python3", "-m", "local_board.cli", *args],
                          cwd=cwd, env=_env(), capture_output=True, text=True)


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.assertEqual(run_cli("init", cwd=self.root).returncode, 0)
        self.state = self.root / ".local-board" / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_force_it_prints_a_plan_and_changes_nothing(self):
        result = run_cli("reset", cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--force", result.stderr)
        self.assertTrue((self.state / "board.db").exists())

    def test_force_moves_state_aside_so_it_can_be_recovered(self):
        result = run_cli("reset", "--force", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.exists())
        moved = list(self.state.parent.glob("state.removed-*"))
        self.assertEqual(len(moved), 1)
        self.assertTrue((moved[0] / "board.db").exists())

    def test_purge_deletes_state_outright(self):
        self.assertEqual(run_cli("reset", "--purge", "--force", cwd=self.root).returncode, 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.state.parent.glob("state.removed-*")), [])

    def test_all_removes_everything_init_created(self):
        result = run_cli("reset", "--all", "--purge", "--force", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in TEMPLATES:
            self.assertFalse((self.root / relative).exists(), relative)
        self.assertFalse((self.root / ".local-board").exists())
        self.assertNotIn("Local Board runtime",
                         (self.root / ".gitignore").read_text(encoding="utf-8"))

    def test_reset_then_init_yields_a_working_board(self):
        run_cli("reset", "--all", "--purge", "--force", cwd=self.root)
        self.assertEqual(run_cli("init", cwd=self.root).returncode, 0)
        status = run_cli("status", "--json", cwd=self.root)
        self.assertEqual(json.loads(status.stdout)["schema_version"], 4)

    def test_an_edited_agents_file_is_kept_not_deleted(self):
        agents = self.root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8") + "\n# team policy\n",
                          encoding="utf-8")
        result = run_cli("reset", "--all", "--purge", "--force", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(agents.exists())
        self.assertIn("team policy", agents.read_text(encoding="utf-8"))

    def test_a_legacy_database_can_be_cleared_and_reinitialized(self):
        import sqlite3

        run_cli("reset", "--purge", "--force", cwd=self.root)
        self.state.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state / "board.db") as db:
            db.execute("PRAGMA user_version=2")
        self.assertEqual(run_cli("init", cwd=self.root).returncode, 1)
        self.assertEqual(run_cli("reset", "--purge", "--force", cwd=self.root).returncode, 0)
        self.assertEqual(run_cli("init", cwd=self.root).returncode, 0)

    def test_reset_refuses_while_a_server_holds_the_state(self):
        port = 8797
        proc = subprocess.Popen(
            ["python3", "-m", "local_board.cli", "serve", "--port", str(port)],
            cwd=self.root, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not (self.state / "server.json").exists():
                time.sleep(0.1)
            result = run_cli("reset", "--force", cwd=self.root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("still running", result.stderr)
            self.assertTrue((self.state / "board.db").exists())
        finally:
            proc.send_signal(signal.SIGINT)
            proc.communicate(timeout=10)

    def test_reset_on_a_repository_without_a_board_is_a_no_op(self):
        run_cli("reset", "--all", "--purge", "--force", cwd=self.root)
        result = run_cli("reset", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to remove", result.stdout)

    def test_plan_is_available_as_json(self):
        result = run_cli("reset", "--all", "--json", cwd=self.root)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["removed"], [])
        self.assertIn("state", {item["kind"] for item in payload["planned"]})


if __name__ == "__main__":
    unittest.main()
