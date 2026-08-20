import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_board.repository import RepositoryNotFound, resolve_database_path


class RepositoryPathTest(unittest.TestCase):
    def test_cli_path_has_highest_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = resolve_database_path(Path(tmp) / "cli.db", environ={"LOCAL_BOARD_DB": "/ignored.db"})
            self.assertEqual(path, (Path(tmp) / "cli.db").resolve())

    def test_environment_path_precedes_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = resolve_database_path(environ={"LOCAL_BOARD_DB": str(Path(tmp) / "env.db")})
            self.assertEqual(path, (Path(tmp) / "env.db").resolve())

    def test_non_git_directory_uses_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("local_board.repository.Repository.discover", side_effect=RepositoryNotFound("no repo")):
                path = resolve_database_path(environ={}, start=tmp)
            self.assertEqual(path, Path(tmp) / ".local-board" / "state" / "board.db")
