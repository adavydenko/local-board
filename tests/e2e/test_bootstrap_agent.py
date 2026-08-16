import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
import socket

from local_board.db import Board
from local_board.repository import Repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BootstrapAgentE2ETest(unittest.TestCase):
    def test_repository_bootstrap_and_agent_issue_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "product"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

            initialized = subprocess.run(
                [sys.executable, "-m", "local_board.cli", "init"],
                cwd=repo, env=env, check=True, capture_output=True, text=True,
            )
            path = Repository.discover(repo).database_path
            self.assertIn(str(path), initialized.stdout)

            actor_result = subprocess.run(
                [sys.executable, "-m", "local_board.cli", "actor", "e2e-agent"],
                cwd=repo, env=env, check=True, capture_output=True, text=True,
            )
            token = actor_result.stdout.split("Token (shown once): ", 1)[1].strip()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            server = subprocess.Popen(
                [sys.executable, "-m", "local_board.cli", "serve", "--port", str(port)],
                cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            )
            calls = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_project", "arguments": {"key": "E2E", "name": "Product"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "create_issue", "arguments": {"project_id": 1, "title": "First agent task", "type": "task"}}},
            ]
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            try:
                for _ in range(50):
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/", timeout=.2): break
                    except Exception: time.sleep(.05)
                else: self.fail("Local Board server did not start")
                for call in calls:
                    request = Request(f"http://127.0.0.1:{port}/mcp", data=json.dumps(call).encode(), headers=headers)
                    with urlopen(request, timeout=2) as response:
                        self.assertFalse(json.load(response)["result"].get("isError", False))
            finally:
                server.terminate()
                try: server.wait(timeout=3)
                except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=3)
            board = Board(path)
            self.assertEqual(board.list_projects()[0]["key"], "E2E")
            self.assertEqual(board.list_issues()[0]["title"], "First agent task")
