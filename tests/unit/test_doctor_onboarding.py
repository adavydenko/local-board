import tempfile
import unittest
from pathlib import Path

from local_board.config import ConfigService, default_config, load_config
from local_board.db import Board
from local_board.doctor import run_doctor
from local_board.onboarding import TEMPLATES, install_onboarding


class DoctorTest(unittest.TestCase):
    def test_offline_doctor_validates_config_database_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config_path = root / "project.toml"
            config_path.write_text(default_config("Doctor", "DOC"))
            board = Board(root / "board.db"); board.init()
            ConfigService(board).apply(load_config(config_path))
            result = run_doctor(board, config_path, online=False)
            self.assertTrue(result["ok"])
            statuses = {item["name"]: item["status"] for item in result["checks"]}
            self.assertEqual(statuses["config"], "pass")
            self.assertEqual(statuses["database_integrity"], "pass")
            self.assertEqual(statuses["foreign_keys"], "pass")
            self.assertEqual(statuses["config_drift"], "pass")
            self.assertEqual(statuses["mcp_connectivity"], "skip")

    def test_invalid_config_fails_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config_path = root / "project.toml"; config_path.write_text("schema_version = 999\n")
            board = Board(root / "board.db"); board.init()
            result = run_doctor(board, config_path, online=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["checks"][0]["status"], "fail")


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
            install_onboarding(root, force=True)
            self.assertIn("name: local-board", skill.read_text())

