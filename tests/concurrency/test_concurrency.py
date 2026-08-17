import concurrent.futures
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path

from local_board.db import Board, ConflictError


def create_issues_in_process(path: str, actor_id: int, project_id: int, count: int):
    board = Board(path)
    return [board.create_issue(actor_id, project_id, f"process-{multiprocessing.current_process().pid}-{index}") for index in range(count)]


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.db"
        self.board = Board(self.path); self.board.init()
        self.actors = [self.board.create_actor(f"agent-{index}") for index in range(16)]
        self.project = self.board.create_project(self.actors[0]["id"], "RACE", "Concurrency")

    def tearDown(self):
        self.tmp.cleanup()

    def test_threads_allocate_unique_numbers_and_positions(self):
        workers = 16
        barrier = threading.Barrier(workers)

        def create(index):
            barrier.wait(timeout=5)
            return Board(self.path).create_issue(self.actors[index]["id"], self.project["id"], f"thread-{index}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            issues = list(pool.map(create, range(workers)))
        self.assertEqual(sorted(issue["number"] for issue in issues), list(range(1, workers + 1)))
        self.assertEqual(len({issue["position"] for issue in issues}), workers)

    def test_processes_allocate_unique_numbers(self):
        workers, per_worker = 4, 8
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(create_issues_in_process, str(self.path), self.actors[index]["id"], self.project["id"], per_worker) for index in range(workers)]
            issues = [issue for future in futures for issue in future.result(timeout=20)]
        self.assertEqual(len(issues), workers * per_worker)
        self.assertEqual(sorted(issue["number"] for issue in issues), list(range(1, workers * per_worker + 1)))
        with self.board.connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])

    def test_stale_update_is_rejected_without_lost_data(self):
        issue = self.board.create_issue(self.actors[0]["id"], self.project["id"], "original")
        revision = issue["revision"]
        barrier = threading.Barrier(2)

        def update(index):
            barrier.wait(timeout=5)
            try:
                result = Board(self.path).update_issue(self.actors[index]["id"], issue["id"], expected_revision=revision, title=f"writer-{index}")
                return "ok", result
            except ConflictError as exc:
                return "conflict", str(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, (0, 1)))
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "ok"])
        current = self.board.get_issue(issue["id"])
        self.assertIn(current["title"], {"writer-0", "writer-1"})
        self.assertEqual(current["revision"], revision + 1)

    def test_only_one_agent_can_claim_an_issue_revision(self):
        issue = self.board.create_issue(self.actors[0]["id"], self.project["id"], "claim me")
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

    def test_only_one_transition_can_consume_a_revision(self):
        issue = self.board.create_issue(self.actors[0]["id"], self.project["id"], "transition me")
        barrier = threading.Barrier(2)

        def transition(index):
            barrier.wait(timeout=5)
            try:
                result = Board(self.path).transition_issue(self.actors[index]["id"], issue["id"], "todo", issue["revision"])
                return "ok", result["revision"]
            except ConflictError:
                return "conflict", None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(transition, (1, 2)))
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "ok"])
        current = self.board.get_issue(issue["id"])
        self.assertEqual((current["status"], current["revision"]), ("todo", issue["revision"] + 1))


if __name__ == "__main__":
    unittest.main()
