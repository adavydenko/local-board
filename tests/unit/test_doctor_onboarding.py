import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from local_board.config import ConfigService, default_config, load_config
from local_board.db import Board
from local_board.doctor import run_doctor
from local_board.onboarding import TEMPLATES, install_onboarding
from local_board.web import make_handler


class DoctorTest(unittest.TestCase):
    def test_offline_doctor_validates_config_database_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "project.toml"
            config_path.write_text(default_config("Doctor", "DOC"))
            board = Board(root / "board.db")
            board.init()
            ConfigService(board).apply(load_config(config_path))
            result = run_doctor(board, config_path, online=False)
            self.assertTrue(result["ok"])
            statuses = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(statuses["config"], "pass")
            self.assertEqual(statuses["board"], "pass")
            self.assertEqual(statuses["database_schema"], "pass")
            self.assertEqual(statuses["database_integrity"], "pass")
            self.assertEqual(statuses["foreign_keys"], "pass")
            self.assertEqual(statuses["config_drift"], "pass")
            self.assertEqual(statuses["mcp_connectivity"], "skip")

    def test_invalid_config_fails_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "project.toml"
            config_path.write_text("schema_version = 999\n")
            board = Board(root / "board.db")
            board.init()
            result = run_doctor(board, config_path, online=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["checks"][0]["status"], "fail")

    def test_unconfigured_board_reports_fail_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "project.toml"
            config_path.write_text(default_config("Doctor", "DOC"))
            board = Board(root / "board.db")
            board.init()
            result = run_doctor(board, config_path, online=False)
            statuses = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(statuses["board"], "fail")
            self.assertFalse(result["ok"])

    def test_doctor_checks_onboarding_files_under_local_board_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_onboarding(root)
            local_board_dir = root / ".local-board"
            local_board_dir.mkdir(exist_ok=True)
            config_path = local_board_dir / "project.toml"
            config_path.write_text(default_config("Doctor", "DOC"))
            board = Board(local_board_dir / "state" / "board.db")
            board.init()
            ConfigService(board).apply(load_config(config_path))
            result = run_doctor(board, config_path, online=False)
            statuses = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(statuses["agent_policy"], "pass")
            self.assertEqual(statuses["agent_skill"], "pass")
            self.assertEqual(statuses["agent_discovery"], "pass")
            self.assertTrue(result["ok"])

    def test_doctor_warns_when_agents_md_missing_the_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_onboarding(root)
            (root / "AGENTS.md").write_text("unrelated human policy")
            local_board_dir = root / ".local-board"
            local_board_dir.mkdir(exist_ok=True)
            config_path = local_board_dir / "project.toml"
            config_path.write_text(default_config("Doctor", "DOC"))
            board = Board(local_board_dir / "state" / "board.db")
            board.init()
            ConfigService(board).apply(load_config(config_path))
            result = run_doctor(board, config_path, online=False)
            statuses = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(statuses["agent_discovery"], "warn")


class DoctorOnlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "project.toml"
        self.config_path.write_text(default_config("Doctor", "DOC"))
        self.board = Board(self.root / "board.db")
        self.board.init()
        ConfigService(self.board).apply(load_config(self.config_path))
        self.actor = self.board.create_actor("doctor-agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_online_doctor_passes_mcp_checks_with_valid_token(self):
        result = run_doctor(
            self.board, self.config_path, url=self.url, token=self.actor["token"], online=True,
        )
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["mcp_initialize"], "pass")
        self.assertEqual(statuses["mcp_tools"], "pass")
        self.assertTrue(result["ok"])

    def test_online_doctor_without_token_fails_mcp_auth(self):
        result = run_doctor(self.board, self.config_path, url=self.url, token=None, online=True)
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["mcp_auth"], "fail")
        self.assertFalse(result["ok"])

    def test_online_doctor_with_unreachable_url_fails_connectivity_not_crash(self):
        result = run_doctor(
            self.board, self.config_path,
            url="http://127.0.0.1:1/mcp", token=self.actor["token"], online=True,
        )
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["mcp_connectivity"], "fail")
        self.assertFalse(result["ok"])

    def test_online_doctor_with_rejected_token_reports_http_error(self):
        result = run_doctor(
            self.board, self.config_path,
            url=self.url, token="not-a-real-token", online=True,
        )
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["mcp_connectivity"], "fail")
        connectivity = next(item for item in result["checks"] if item["name"] == "mcp_connectivity")
        self.assertIn("HTTP 401", connectivity["message"])
        self.assertFalse(result["ok"])


class OnboardingTest(unittest.TestCase):
    def test_installs_all_templates_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = install_onboarding(root)
            self.assertEqual(set(created), {root / relative for relative in TEMPLATES})
            skill = root / ".agents/skills/local-board/SKILL.md"
            skill.write_text("custom")
            self.assertEqual(install_onboarding(root), [])
            self.assertEqual(skill.read_text(), "custom")
            agents = root / "AGENTS.md"
            agents.write_text("custom root policy")
            install_onboarding(root, force=True)
            self.assertIn("name: local-board", skill.read_text())
            self.assertEqual(agents.read_text(), "custom root policy")


if __name__ == "__main__":
    unittest.main()


class DoctorStaleServerTest(unittest.TestCase):
    def test_stale_server_json_yields_warning(self):
        import json as jsonlib
        import tempfile
        from pathlib import Path

        from local_board.config import ConfigService, default_config, load_config
        from local_board.db import Board
        from local_board.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = Board(root / "state" / "board.db")
            board.init()
            config_path = root / "project.toml"
            config_path.write_text(default_config("App", "APP"), encoding="utf-8")
            ConfigService(board).apply(load_config(config_path))
            stale = {"url": "http://127.0.0.1:1", "pid": 2 ** 22 + 12345, "started_at": "x"}
            (board.path.parent / "server.json").write_text(jsonlib.dumps(stale), encoding="utf-8")
            result = run_doctor(board, config_path, online=False)
            server_check = next(item for item in result["checks"] if item["name"] == "server")
            self.assertEqual(server_check["status"], "warn")
            self.assertIn("uncleanly", server_check["message"])
