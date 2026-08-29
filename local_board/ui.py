"""Terminal presentation: colour, layout, and the error/hint vocabulary.

Dependency-free on purpose. Local Board ships with no third-party requirements,
so the CLI paints itself with ANSI escapes it knows how to switch off, and falls
back to ASCII whenever the stream cannot carry the nicer glyphs.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Iterable, Sequence

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "cyan": "36",
}

_GLYPHS = {
    "pass": ("✔", "ok  "),
    "fail": ("✘", "fail"),
    "warn": ("⚠", "warn"),
    "skip": ("·", "skip"),
}

_STATUS_COLOURS = {"pass": "green", "fail": "red", "warn": "yellow", "skip": "dim"}


class Theme:
    """Colour and glyph decisions for one pair of output streams."""

    def __init__(self, *, colour: bool = False, unicode: bool = False) -> None:
        self.colour = colour
        self.unicode = unicode

    def paint(self, text: str, *styles: str) -> str:
        if not self.colour or not styles:
            return text
        prefix = "".join(f"\033[{_CODES[style]}m" for style in styles)
        return f"{prefix}{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def cyan(self, text: str) -> str:
        return self.paint(text, "cyan")

    def glyph(self, status: str) -> str:
        fancy, plain = _GLYPHS[status]
        return self.paint(fancy if self.unicode else plain, _STATUS_COLOURS[status])


def _supports_colour(when: str, stream: IO[str], environ: dict[str, str]) -> bool:
    if when == "always":
        return True
    if when == "never":
        return False
    if environ.get("NO_COLOR"):
        return False
    if environ.get("FORCE_COLOR"):
        return True
    if environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _supports_unicode(stream: IO[str]) -> bool:
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "✔✘⚠".encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


theme = Theme()


def configure(when: str = "auto", *, stream: IO[str] | None = None,
              environ: dict[str, str] | None = None) -> Theme:
    """Set the process-wide theme from --color, the environment, and the tty."""
    target = stream or sys.stdout
    env = os.environ if environ is None else environ
    theme.colour = _supports_colour(when, target, env)
    theme.unicode = _supports_unicode(target)
    return theme


# -- message vocabulary -------------------------------------------------------
# One shape for every diagnostic: a severity-tagged first line, then indented
# `hint:` lines that name the next command to run. Agents parse the prefixes;
# humans read the hint.

def error(message: str, *hints: str, stream: IO[str] | None = None) -> None:
    target = stream or sys.stderr
    print(f"{theme.paint('error', 'bold', 'red')}: {message}", file=target)
    for hint in hints:
        print(f"  {theme.paint('hint', 'bold', 'cyan')}: {hint}", file=target)


def warn(message: str, *hints: str, stream: IO[str] | None = None) -> None:
    target = stream or sys.stderr
    print(f"{theme.paint('warning', 'bold', 'yellow')}: {message}", file=target)
    for hint in hints:
        print(f"  {theme.paint('hint', 'bold', 'cyan')}: {hint}", file=target)


def note(message: str, stream: IO[str] | None = None) -> None:
    print(message, file=stream or sys.stdout)


# -- layout -------------------------------------------------------------------

def heading(text: str, stream: IO[str] | None = None) -> None:
    print(theme.bold(text), file=stream or sys.stdout)


def fields(rows: Sequence[tuple[str, str]], *, indent: str = "  ",
           stream: IO[str] | None = None) -> None:
    """Print an aligned label/value block, the shape `status` and `serve` share."""
    if not rows:
        return
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{indent}{theme.dim(label.ljust(width))}  {value}", file=stream or sys.stdout)


def listing(rows: Iterable[tuple[str, str]], *, indent: str = "  ", width: int = 12,
            stream: IO[str] | None = None) -> None:
    """Print a `name  summary` block: the command list in help output."""
    for name, summary in rows:
        print(f"{indent}{theme.cyan(name.ljust(width))}  {summary}", file=stream or sys.stdout)


def check(status: str, name: str, message: str, *, width: int = 0,
          stream: IO[str] | None = None) -> None:
    print(f"  {theme.glyph(status)}  {name.ljust(width)}  {message}", file=stream or sys.stdout)
