import tomllib
import tempfile
import unittest
from pathlib import Path

from local_board import __version__
from local_board.db import Board
from local_board.mcp import handle


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VersionTest(unittest.TestCase):
    def test_package_and_mcp_versions_match(self):
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
            package_version = tomllib.load(file)["project"]["version"]
        self.assertEqual(package_version, __version__)

        with tempfile.TemporaryDirectory() as directory:
            board = Board(Path(directory) / "board.db")
            board.init()
            actor = board.create_actor("version-check")
            response = handle(board, actor["id"], {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(response["result"]["serverInfo"]["version"], package_version)


if __name__ == "__main__":
    unittest.main()
