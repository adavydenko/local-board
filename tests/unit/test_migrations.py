import multiprocessing
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from local_board.db import Board, SCHEMA_VERSION


EXPECTED_TABLES = {
    "board", "actors", "statuses", "milestones", "labels", "issues",
    "issue_labels", "comments", "dependencies", "git_links", "activity", "state_counters",
}


def _init_worker(path: str, barrier) -> bool:
    """Module-level so it can be pickled by the process pool (see tests/concurrency)."""
    barrier.wait(timeout=10)
    Board(path).init()
    return True


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_on_fresh_database_creates_full_schema(self):
        board = Board(self.path)
        board.init()
        self.assertEqual(board.schema_version(), SCHEMA_VERSION)
        with board.connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(EXPECTED_TABLES <= tables)

    def test_init_rejects_a_pre_0_1_database(self):
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(RuntimeError, "back it up"):
            Board(self.path).init()

    def test_pre_0_1_rejection_names_the_directory_to_move_aside(self):
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaises(RuntimeError) as caught:
            Board(self.path).init()
        self.assertIn(str(self.path.parent), str(caught.exception))

    def test_doctor_reports_a_pre_0_1_database_instead_of_a_raw_sqlite_error(self):
        from local_board.doctor import run_doctor

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA user_version=2")
        result = run_doctor(Board(self.path), self.path.parent / "project.toml", online=False)
        self.assertFalse(result["ok"])
        schema = [c for c in result["checks"] if c["name"] == "database_schema"]
        self.assertEqual(len(schema), 1)
        self.assertIn("predates the 0.1.0 board format", schema[0]["message"])
        self.assertNotIn("no such table", json.dumps(result))

    def test_init_rejects_a_newer_database(self):
        with sqlite3.connect(self.path) as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            Board(self.path).init()

    def test_concurrent_init_from_many_processes_all_succeed(self):
        workers = 8
        with multiprocessing.Manager() as manager:
            barrier = manager.Barrier(workers)
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_init_worker, str(self.path), barrier) for _ in range(workers)]
                results = [future.result(timeout=30) for future in futures]
        self.assertTrue(all(results))
        board = Board(self.path)
        self.assertEqual(board.schema_version(), SCHEMA_VERSION)
        with board.connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
