"""Git repository discovery and Local Board path resolution."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepositoryNotFound(RuntimeError):
    """Raised when a command requiring a Git repository runs outside one."""


@dataclass(frozen=True)
class Repository:
    root: Path
    git_common_dir: Path

    @classmethod
    def discover(cls, start: str | Path | None = None) -> "Repository":
        cwd = Path(start or Path.cwd()).resolve()

        def git(*args: str) -> Path:
            try:
                result = subprocess.run(
                    ["git", "-C", str(cwd), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise RepositoryNotFound(f"not inside a Git repository: {cwd}") from exc
            return Path(result.stdout.strip()).resolve()

        return cls(
            root=git("rev-parse", "--show-toplevel"),
            git_common_dir=git("rev-parse", "--path-format=absolute", "--git-common-dir"),
        )

    @property
    def database_path(self) -> Path:
        """Runtime state owned by the server started for this checkout."""
        return self.root / ".local-board" / "state" / "board.db"

    @property
    def config_path(self) -> Path:
        return self.root / ".local-board" / "project.toml"


def resolve_database_path(
    cli_value: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
    start: str | Path | None = None,
) -> Path:
    """Resolve CLI > environment > repository-local > cwd fallback precedence."""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ if environ is None else environ
    if env.get("LOCAL_BOARD_DB"):
        return Path(env["LOCAL_BOARD_DB"]).expanduser().resolve()
    try:
        return Repository.discover(start).database_path
    except RepositoryNotFound:
        return Path(start or Path.cwd()).resolve() / ".local-board" / "state" / "board.db"
