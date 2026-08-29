"""The inverse of `init`: remove what it created, and nothing else.

Driven by the same inventory `init` installs from, so the two cannot drift —
whatever `onboarding.TEMPLATES` adds, this offers to take away. Runtime state
is moved aside rather than deleted by default: a board nobody meant to lose is
one `mv` away from coming back, and `--purge` is there when it is genuinely junk.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .onboarding import GITIGNORE_BLOCK, GITIGNORE_MARKER, TEMPLATES
from importlib.resources import files


class ServerRunning(RuntimeError):
    """Raised when a live server still holds the state being removed."""


@dataclass(frozen=True)
class Removal:
    path: Path
    kind: str
    action: str  # "move" | "delete" | "edit"
    note: str = ""

    def as_dict(self, root: Path) -> dict:
        return {"path": str(self.path), "kind": self.kind, "action": self.action,
                "note": self.note}


def server_pid(state_dir: Path) -> int | None:
    """The pid of a server still serving this state, or None if nothing is live."""
    discovery = state_dir / "server.json"
    if not discovery.exists():
        return None
    try:
        pid = int(json.loads(discovery.read_text(encoding="utf-8")).get("pid", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def _agents_bridge_is_pristine(path: Path) -> bool:
    """True when AGENTS.md still holds exactly what init wrote.

    Root instructions may carry unrelated human policy — init refuses to
    overwrite them, so reset refuses to delete them.
    """
    template = files("local_board").joinpath("templates", "local-board-agents-bridge.md")
    try:
        return path.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
    except OSError:
        return False


def plan(root: Path, state_dir: Path, config_path: Path, *, everything: bool,
         purge: bool) -> list[Removal]:
    """Everything reset would touch, in the order it would touch it."""
    removals: list[Removal] = []
    if state_dir.exists():
        removals.append(Removal(state_dir, "state", "delete" if purge else "move",
                                "database, WAL, server discovery, crash log"))
    if not everything:
        return removals

    backups = state_dir.parent / "backups"
    if backups.exists():
        removals.append(Removal(backups, "backups", "delete" if purge else "move"))
    if config_path.exists():
        removals.append(Removal(config_path, "config", "delete"))

    for relative in TEMPLATES:
        path = root / relative
        if not path.exists():
            continue
        if relative == Path("AGENTS.md") and not _agents_bridge_is_pristine(path):
            removals.append(Removal(path, "onboarding", "keep",
                                    "has local edits; remove it yourself if you meant to"))
            continue
        removals.append(Removal(path, "onboarding", "delete"))

    gitignore = root / ".gitignore"
    if gitignore.exists() and GITIGNORE_MARKER in gitignore.read_text(encoding="utf-8"):
        removals.append(Removal(gitignore, "gitignore", "edit", "drop the Local Board block"))
    return removals


def apply(removals: list[Removal], *, stamp: str | None = None) -> list[dict]:
    """Carry out a plan. Moves land beside the original as <name>.removed-<stamp>."""
    stamp = stamp or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    done: list[dict] = []
    for removal in removals:
        if removal.action == "keep":
            done.append({"path": str(removal.path), "action": "kept", "note": removal.note})
            continue
        if removal.action == "move":
            destination = removal.path.with_name(f"{removal.path.name}.removed-{stamp}")
            shutil.move(str(removal.path), str(destination))
            done.append({"path": str(removal.path), "action": "moved",
                         "destination": str(destination)})
            continue
        if removal.action == "edit":
            _strip_gitignore_block(removal.path)
            done.append({"path": str(removal.path), "action": "edited"})
            continue
        if removal.path.is_dir():
            shutil.rmtree(removal.path)
        else:
            removal.path.unlink()
        done.append({"path": str(removal.path), "action": "deleted"})
    _prune_empty_dirs(removals)
    return done


def _strip_gitignore_block(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if GITIGNORE_BLOCK in content:
        content = content.replace(GITIGNORE_BLOCK, "\n")
    else:
        # Hand-edited spacing: drop the marker and the entries it introduced.
        kept, skipping = [], False
        for line in content.splitlines():
            if line.strip() == GITIGNORE_MARKER:
                skipping = True
                continue
            if skipping:
                if line.startswith(".local-board/"):
                    continue
                skipping = False
            kept.append(line)
        content = "\n".join(kept)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _prune_empty_dirs(removals: list[Removal]) -> None:
    """Leave no empty scaffolding behind, but never remove a directory holding
    something we did not put there."""
    candidates = {removal.path.parent for removal in removals}
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        for directory in (path, *path.parents):
            if directory.name in ("", "/"):
                break
            try:
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
                else:
                    break
            except OSError:
                break
