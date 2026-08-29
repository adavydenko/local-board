import concurrent.futures
import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from local_board.db import Board, ConflictError, DatabaseBusyError


def _create_issues_in_process(path: str, actor_id: int, count: int, barrier) -> list:
    """Module-level so it can be pickled by ProcessPoolExecutor."""
    board = Board(path)
    barrier.wait(timeout=10)
    return [
        board.create_issue(actor_id, f"process-{multiprocessing.current_process().pid}-{index}")
        for index in range(count)
    ]


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.db"
        self.board = Board(self.path)
        self.board.init()
        self.board.configure_board("RACE", "Concurrency")
        self.actors = [self.board.create_actor(f"agent-{index}") for index in range(16)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_threads_allocate_unique_numbers_and_positions(self):
        workers, repetitions = 16, 3
        all_issues = []
        for repetition in range(repetitions):
            barrier = threading.Barrier(workers)

            def create(index):
                barrier.wait(timeout=5)
                return Board(self.path).create_issue(self.actors[index]["id"], f"thread-{repetition}-{index}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                all_issues.extend(pool.map(create, range(workers)))
        expected = list(range(1, workers * repetitions + 1))
        self.assertEqual(sorted(issue["number"] for issue in all_issues), expected)
        self.assertEqual(sorted(issue["position"] for issue in all_issues), expected)
        self.assertEqual(len(self.board.list_issues()), len(expected))

    def test_processes_allocate_unique_numbers(self):
        workers, per_worker = 8, 4
        with multiprocessing.Manager() as manager:
            barrier = manager.Barrier(workers)
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(_create_issues_in_process, str(self.path), self.actors[index]["id"], per_worker, barrier)
                    for index in range(workers)
                ]
                issues = [issue for future in futures for issue in future.result(timeout=30)]
        self.assertEqual(len(issues), workers * per_worker)
        self.assertEqual(sorted(issue["number"] for issue in issues), list(range(1, workers * per_worker + 1)))
        with self.board.connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])

    def test_only_one_claim_wins_a_revision_race(self):
        issue = self.board.create_issue(self.actors[0]["id"], "claim me")
        barrier = threading.Barrier(2)

        def claim(index):
            barrier.wait(timeout=5)
            try:
                result = Board(self.path).claim_issue(self.actors[index]["id"], issue["id"], issue["revision"])
                return "ok", result["assignee_id"]
            except ConflictError:
                return "conflict", self.actors[index]["id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (1, 2)))
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "ok"])
        winner = next(result[1] for result in results if result[0] == "ok")
        current = self.board.get_issue(issue["id"])
        self.assertEqual(current["assignee_id"], winner)
        self.assertIsNotNone(current["claim_expires_at"])

    def test_only_one_update_wins_a_revision_race(self):
        issue = self.board.create_issue(self.actors[0]["id"], "original title")
        revision = issue["revision"]
        barrier = threading.Barrier(2)

        def update(index):
            barrier.wait(timeout=5)
            try:
                result = Board(self.path).update_issue(
                    self.actors[index]["id"], issue["id"], expected_revision=revision, title=f"writer-{index}"
                )
                return "ok", result
            except ConflictError as exc:
                return "conflict", str(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, (0, 1)))
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "ok"])
        current = self.board.get_issue(issue["id"])
        self.assertIn(current["title"], {"writer-0", "writer-1"})
        self.assertEqual(current["revision"], revision + 1)

    def test_readers_alongside_writers_preserve_integrity(self):
        workers = 16
        barrier = threading.Barrier(workers)

        def work(index):
            board = Board(self.path)
            barrier.wait(timeout=5)
            if index < 8:
                for sequence in range(10):
                    board.create_issue(self.actors[index]["id"], f"stress-{index}-{sequence}")
            else:
                for _ in range(15):
                    board.dashboard()
                    board.list_issues()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, range(workers)))
        with self.board.connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(db.execute("SELECT count(*) FROM issues").fetchone()[0], 80)

    def test_lock_retry_exhaustion_has_a_clear_error(self):
        locked = sqlite3.connect(self.path, isolation_level=None)
        locked.execute("BEGIN IMMEDIATE")
        try:
            contender = Board(self.path, busy_timeout_ms=5, max_lock_retries=1, retry_base_seconds=0.001)
            with self.assertRaisesRegex(DatabaseBusyError, "after 2 attempts"):
                contender.create_issue(self.actors[0]["id"], "cannot acquire lock")
        finally:
            locked.rollback()
            locked.close()


if __name__ == "__main__":
    unittest.main()
