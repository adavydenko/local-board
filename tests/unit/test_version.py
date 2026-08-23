import tomllib
import unittest
from pathlib import Path

from local_board import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VersionTest(unittest.TestCase):
    def test_package_and_module_versions_match(self):
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
            package_version = tomllib.load(file)["project"]["version"]
        self.assertEqual(package_version, __version__)


if __name__ == "__main__":
    unittest.main()
