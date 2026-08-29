import tomllib
import unittest
from fnmatch import fnmatch
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "local_board"
PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def _glob_matches(pattern: str, relative: str) -> bool:
    """Segment-wise glob match: * stays within one path segment, ** spans many.

    fnmatch alone is wrong here — its * crosses '/' and would happily claim a
    flat 'static/*' covers 'static/css/tokens.css', which is exactly the wheel
    trap this test exists to catch.
    """
    def match(patterns: list[str], parts: list[str]) -> bool:
        if not patterns:
            return not parts
        head, *rest = patterns
        if head == "**":
            return any(match(rest, parts[skip:]) for skip in range(len(parts) + 1))
        return bool(parts) and fnmatch(parts[0], head) and match(rest, parts[1:])

    return match(pattern.split("/"), relative.split("/"))


class PackageDataTest(unittest.TestCase):
    """Every shipped asset must be covered by the package-data globs.

    A file under static/ that no glob matches works fine in a dev checkout and
    silently disappears from the built wheel — the classic trap when the UI
    grows nested css/ and js/ directories.
    """

    def test_every_static_and_template_file_matches_a_package_data_glob(self):
        with PYPROJECT.open("rb") as handle:
            globs = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["local_board"]
        shipped = [path for base in ("static", "templates")
                   for path in (PACKAGE_ROOT / base).rglob("*") if path.is_file()]
        self.assertTrue(shipped, "expected shipped asset files to exist")
        for path in shipped:
            relative = path.relative_to(PACKAGE_ROOT).as_posix()
            self.assertTrue(
                any(_glob_matches(pattern, relative) for pattern in globs),
                f"{relative} is not covered by package-data globs {globs}",
            )

    def test_matcher_rejects_the_flat_glob_trap(self):
        self.assertFalse(_glob_matches("static/*", "static/css/tokens.css"))
        self.assertTrue(_glob_matches("static/**/*", "static/css/tokens.css"))
        self.assertTrue(_glob_matches("static/**/*", "static/index.html"))
