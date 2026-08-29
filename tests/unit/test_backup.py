import json
import tempfile
import unittest
from pathlib import Path

from local_board.backup import create_backup, restore_backup
from local_board.db import Board


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.board = Board(self.root / "state" / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.actor = self.board.create_actor("administrator")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_backup_with_manifest(self):
        self.board.create_issue(self.actor["id"], "Kept issue")
        backup = self.root / "backups" / "snapshot.db"
        result = create_backup(self.board, backup)
        manifest = json.loads(Path(str(backup) + ".json").read_text())
        self.assertEqual(manifest["sha256"], result["sha256"])
        self.board.create_issue(self.actor["id"], "Issue after backup")
        self.assertEqual(len(self.board.list_issues()), 2)
        restore_backup(self.board, backup)
        self.assertEqual([issue["title"] for issue in self.board.list_issues()], ["Kept issue"])

    def test_rejects_non_database_and_tampered_manifest(self):
        invalid = self.root / "invalid.db"
        invalid.write_text("not sqlite")
        with self.assertRaisesRegex(ValueError, "valid SQLite"):
            restore_backup(self.board, invalid)
        backup = self.root / "snapshot.db"
        create_backup(self.board, backup)
        manifest = Path(str(backup) + ".json")
        value = json.loads(manifest.read_text())
        value["sha256"] = "0" * 64
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore_backup(self.board, backup)


if __name__ == "__main__":
    unittest.main()
