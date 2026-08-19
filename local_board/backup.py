from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .db import Board


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"backup does not exist: {path}")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("backup failed SQLite integrity_check")
            required = {"actors", "projects", "issues", "activity"}
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= tables:
                raise ValueError("file is not a Local Board database")
    except sqlite3.DatabaseError as exc:
        raise ValueError("backup is not a valid SQLite database") from exc


def create_backup(board: Board, destination: str | Path) -> dict[str, object]:
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with board.connect() as source, sqlite3.connect(destination) as target:
        source.backup(target)
    _validate(destination)
    manifest = {
        "format": "local-board-backup-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": board.schema_version(),
        "sha256": _sha256(destination),
        "size": destination.stat().st_size,
    }
    destination.with_suffix(destination.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"path": str(destination), **manifest}


def restore_backup(board: Board, source: str | Path) -> dict[str, object]:
    source = Path(source).resolve()
    _validate(source)
    manifest_path = source.with_suffix(source.suffix + ".json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "local-board-backup-v1" or manifest.get("sha256") != _sha256(source):
            raise ValueError("backup manifest checksum mismatch")
    board.path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="restore-", suffix=".db", dir=board.path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as backup, sqlite3.connect(temporary) as target:
            backup.backup(target)
        _validate(temporary)
        os.replace(temporary, board.path)
        Path(str(board.path) + "-wal").unlink(missing_ok=True)
        Path(str(board.path) + "-shm").unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return {"restored": True, "path": str(board.path), "source": str(source), "sha256": _sha256(board.path)}
