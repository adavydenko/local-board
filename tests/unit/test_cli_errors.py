import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    return env


def run_cli(*args, cwd):
    return subprocess.run(
        ["python3", "-m", "local_board.cli", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
    )


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CliErrorHandlingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Local Board Tests"], cwd=self.root, check=True)
        result = run_cli("init", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_actor_prints_clean_error_and_exits_nonzero(self):
        first = run_cli("actor", "dup", "--kind", "agent", cwd=self.root)
        self.assertEqual(first.returncode, 0, first.stderr)

        second = run_cli("actor", "dup", "--kind", "agent", cwd=self.root)
        self.assertEqual(second.returncode, 1)
        self.assertIn("already exists", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertNotIn("Traceback", second.stdout)

    def test_duplicate_actor_json_error_has_conflict_code(self):
        run_cli("actor", "dup", "--kind", "agent", cwd=self.root)
        run_cli("actor", "dup", "--kind", "agent", cwd=self.root)

        third = run_cli("actor", "dup", "--json", cwd=self.root)
        self.assertEqual(third.returncode, 1)
        body = json.loads(third.stdout)
        self.assertEqual(body["error"]["code"], "conflict")


class ServeDiscoveryFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Local Board Tests"], cwd=self.root, check=True)
        result = run_cli("init", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_serve_writes_and_removes_server_json_and_serves_health(self):
        port = free_port()
        server_json = self.root / ".local-board" / "state" / "server.json"
        proc = subprocess.Popen(
            ["python3", "-m", "local_board.cli", "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=self.root,
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not server_json.exists():
                time.sleep(0.1)
            self.assertTrue(server_json.exists(), "server.json was not created in time")

            discovery = json.loads(server_json.read_text(encoding="utf-8"))
            self.assertEqual(discovery["url"], f"http://127.0.0.1:{port}")
            self.assertIn("pid", discovery)
            self.assertIn("started_at", discovery)

            with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                self.assertEqual(response.status, 200)
                body = json.loads(response.read())
                self.assertEqual(body["status"], "ok")

            proc.send_signal(signal.SIGINT)
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertFalse(server_json.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
