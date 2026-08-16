import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_board.db import Board, SCHEMA_VERSION


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_initializes_and_reopens_current_schema(self):
        board = Board(self.path)
        board.init()
        self.assertEqual(board.schema_version(), SCHEMA_VERSION)
        board.init()
        self.assertEqual(board.schema_version(), SCHEMA_VERSION)

    def test_upgrades_unversioned_existing_schema(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE legacy_data(value TEXT)")
        board = Board(self.path)
        board.init()
        self.assertEqual(board.schema_version(), SCHEMA_VERSION)
        with board.connect() as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='actors'").fetchone())
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='legacy_data'").fetchone())

    def test_rejects_newer_database(self):
        with sqlite3.connect(self.path) as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            Board(self.path).init()

