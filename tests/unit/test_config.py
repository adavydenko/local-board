import tempfile
import unittest
from pathlib import Path

from local_board.config import ConfigError, ConfigService, default_config, load_config, validate_config
from local_board.db import Board


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "project.toml"
        self.path.write_text(default_config("Application", "APP"), encoding="utf-8")
        self.board = Board(self.root / "board.db")
        self.board.init()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _minimal_data():
        return {
            "schema_version": 2,
            "board": {"prefix": "APP", "name": "Application"},
            "statuses": [
                {"name": "Todo", "category": "unstarted"},
                {"name": "Done", "category": "completed"},
            ],
        }

    # -- default config and validation -------------------------------------------------

    def test_default_config_is_valid(self):
        config = load_config(self.path)
        self.assertEqual(config.schema_version, 2)
        self.assertEqual(config.board["prefix"], "APP")
        self.assertEqual(config.board["name"], "Application")

    def test_missing_board_table_is_rejected(self):
        data = self._minimal_data()
        del data["board"]
        with self.assertRaisesRegex(ConfigError, r"\[board\]"):
            validate_config(data)

    def test_bad_prefix_is_rejected(self):
        data = self._minimal_data()
        data["board"]["prefix"] = "a"
        with self.assertRaisesRegex(ConfigError, "prefix"):
            validate_config(data)

    def test_unknown_status_category_is_rejected(self):
        data = self._minimal_data()
        data["statuses"][0]["category"] = "mystery"
        with self.assertRaisesRegex(ConfigError, "category"):
            validate_config(data)

    def test_duplicate_status_names_are_rejected(self):
        data = self._minimal_data()
        data["statuses"].append({"name": data["statuses"][0]["name"], "category": "completed"})
        with self.assertRaisesRegex(ConfigError, "unique"):
            validate_config(data)

    def test_no_active_statuses_is_rejected(self):
        data = self._minimal_data()
        data["statuses"] = [{"name": "Done", "category": "completed"}]
        with self.assertRaisesRegex(ConfigError, "active category"):
            validate_config(data)

    def test_bad_label_color_is_rejected(self):
        data = self._minimal_data()
        data["labels"] = [{"key": "bad", "name": "Bad", "color": "red"}]
        with self.assertRaisesRegex(ConfigError, "color"):
            validate_config(data)

    def test_status_name_with_injection_characters_is_rejected(self):
        data = self._minimal_data()
        data["statuses"][0]["name"] = "Todo'; DROP TABLE issues;--"
        with self.assertRaisesRegex(ConfigError, "invalid status name"):
            validate_config(data)

    # -- plan / apply -------------------------------------------------------------------

    def test_apply_creates_board_statuses_and_labels_then_reapply_is_noop(self):
        config = load_config(self.path)
        service = ConfigService(self.board)
        self.assertTrue(service.plan(config)["changed"])
        applied = service.apply(config)
        self.assertTrue(applied["applied"])
        board = self.board.get_board()
        self.assertEqual(board["prefix"], "APP")
        with self.board.connect() as db:
            names = {row[0] for row in db.execute("SELECT name FROM statuses")}
            self.assertIn("Backlog", names)
            labels = {row[0] for row in db.execute("SELECT name FROM labels")}
            self.assertIn("Review required", labels)
        self.assertFalse(service.plan(config)["changed"])
        self.assertFalse(service.apply(config)["applied"])

    def test_renaming_a_status_still_used_by_issues_is_rejected(self):
        service = ConfigService(self.board)
        service.apply(load_config(self.path))
        actor = self.board.create_actor("agent")
        self.board.create_issue(actor["id"], "In use")
        renamed = self.path.read_text().replace('name = "Backlog"', 'name = "Icebox"')
        self.path.write_text(renamed)
        with self.assertRaisesRegex(ValueError, "cannot remove statuses used by issues"):
            service.apply(load_config(self.path))

    def test_manual_edit_of_config_managed_label_is_rejected(self):
        service = ConfigService(self.board)
        service.apply(load_config(self.path))
        actor = self.board.create_actor("agent")
        with self.assertRaisesRegex(ValueError, "managed by"):
            self.board.update_label(actor["id"], "review_required", name="Renamed")


if __name__ == "__main__":
    unittest.main()
