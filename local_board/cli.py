from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .backup import create_backup, restore_backup
from .config import ConfigError, ConfigService, default_config, load_config, suggested_prefix
from .db import Board
from .doctor import run_doctor
from .errors import describe
from .onboarding import install_onboarding
from .repository import Repository, RepositoryNotFound, resolve_database_path
from .web import serve


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-board")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", help="database path (defaults to .local-board/state/board.db)")
    parser.add_argument("--config", help="project config path (defaults to .local-board/project.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    initialize = sub.add_parser("init")
    initialize.add_argument("--force", action="store_true")

    actor = sub.add_parser("actor")
    actor.add_argument("name")
    actor.add_argument("--kind", choices=("agent", "human"), default="agent")
    actor.add_argument("--role", choices=("admin", "member", "viewer"))
    actor.add_argument("--json", action="store_true")

    web = sub.add_parser("serve")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    backup = sub.add_parser("backup")
    backup.add_argument("path", nargs="?")

    restore = sub.add_parser("restore")
    restore.add_argument("path")
    restore.add_argument("--force", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    doctor.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN"))
    doctor.add_argument("--offline", action="store_true")
    doctor.add_argument("--json", action="store_true")

    config_parser = sub.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate")
    config_sub.add_parser("plan")
    apply_config = config_sub.add_parser("apply")
    apply_config.add_argument("--actor", type=int)

    sync = sub.add_parser("sync-branch")
    sync.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN"))

    return parser


def _config_path(args: argparse.Namespace) -> Path:
    if args.config:
        return Path(args.config).resolve()
    try:
        return Repository.discover().config_path
    except RepositoryNotFound:
        return Path(".local-board/project.toml").resolve()


def _require_board(args: argparse.Namespace) -> Board:
    db_path = resolve_database_path(args.db)
    if not db_path.exists():
        raise SystemExit("run `local-board init` first")
    return Board(db_path)


def main() -> None:
    args = _build_parser().parse_args()
    config_path = _config_path(args)

    try:
        _dispatch(args, config_path)
    except SystemExit:
        raise
    except Exception as exc:
        _report_error(args, exc)
        raise SystemExit(1) from exc


def _dispatch(args: argparse.Namespace, config_path: Path) -> None:
    if args.command == "init":
        _run_init(args, config_path)
    elif args.command == "actor":
        _run_actor(args)
    elif args.command == "serve":
        serve(_require_board(args), args.host, args.port)
    elif args.command == "status":
        _run_status(args, config_path)
    elif args.command == "backup":
        _run_backup(args)
    elif args.command == "restore":
        _run_restore(args)
    elif args.command == "doctor":
        _run_doctor_command(args, config_path)
    elif args.command == "config":
        _run_config(args, config_path)
    elif args.command == "sync-branch":
        _run_sync_branch(args)


def _report_error(args: argparse.Namespace, exc: Exception) -> None:
    _, code, message, retryable = describe(exc)
    if code == "internal":
        message = str(exc)
    as_json = getattr(args, "json", False)
    if as_json:
        print(json.dumps({"error": {"code": code, "message": message, "retryable": retryable}}))
    else:
        print(f"error: {message}", file=sys.stderr)
    if code == "internal" and os.environ.get("LOCAL_BOARD_DEBUG"):
        raise exc


def _run_init(args: argparse.Namespace, config_path: Path) -> None:
    try:
        repo = Repository.discover()
    except RepositoryNotFound:
        repo = None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not args.force:
        print(f"Keeping existing {config_path}")
    else:
        name = repo.root.name if repo else Path.cwd().name
        config_path.write_text(default_config(name, suggested_prefix(name)), encoding="utf-8")
        print(f"Created {config_path}")
    if repo:
        _ensure_gitignore(repo.root)
        for path in install_onboarding(repo.root, force=args.force):
            print(f"Created {path}")
    board = Board(resolve_database_path(args.db))
    board.init()
    result = ConfigService(board).apply(load_config(config_path))
    print(f"Initialized {board.path}; applied {len(result['actions'])} configuration action(s)")


def _run_actor(args: argparse.Namespace) -> None:
    board = _require_board(args)
    value = board.create_actor(args.name, args.kind, args.role)
    if args.json:
        print(json.dumps(value))
    else:
        print(f"Actor: {value['name']} ({value['kind']}, {value['role']})")
        print(f"Token (shown once): {value['token']}")


def _run_status(args: argparse.Namespace, config_path: Path) -> None:
    db_path = resolve_database_path(args.db)
    try:
        repo = Repository.discover()
        value = {"repository": str(repo.root), "git_common_dir": str(repo.git_common_dir)}
    except RepositoryNotFound:
        value = {"repository": None, "git_common_dir": None}
    value["config"] = str(config_path)
    if db_path.exists():
        value["database"] = str(db_path)
        value["schema_version"] = Board(db_path).schema_version()
    else:
        value["database"] = "not initialized"
    if args.json:
        print(json.dumps(value))
    else:
        for key, item in value.items():
            print(f"{key}: {item}")


def _run_backup(args: argparse.Namespace) -> None:
    board = _require_board(args)
    destination = (
        Path(args.path).resolve()
        if args.path
        else board.path.parent.parent / "backups" / f"board-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    )
    print(json.dumps(create_backup(board, destination), indent=2))


def _run_restore(args: argparse.Namespace) -> None:
    if not args.force:
        raise SystemExit("restore replaces current state; pass --force")
    board = _require_board(args)
    safety = board.path.parent.parent / "backups" / f"pre-restore-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    if board.path.exists():
        create_backup(board, safety)
    print(json.dumps(restore_backup(board, args.path), indent=2))


def _run_doctor_command(args: argparse.Namespace, config_path: Path) -> None:
    db_path = resolve_database_path(args.db)
    if db_path.exists():
        result = run_doctor(
            Board(db_path), config_path, url=args.url, token=args.token, online=not args.offline
        )
    else:
        result = {
            "ok": False,
            "checks": [{
                "name": "database",
                "status": "fail",
                "message": "database not initialized; run `local-board init` first",
            }],
        }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        icons = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}
        for check in result["checks"]:
            print(f"[{icons[check['status']]}] {check['name']}: {check['message']}")
    if not result["ok"]:
        raise SystemExit(1)


def _run_config(args: argparse.Namespace, config_path: Path) -> None:
    try:
        if args.config_command == "validate":
            config = load_config(config_path)
            print(f"Valid: {config.path} (schema {config.schema_version}, digest {config.digest})")
            return
        config = load_config(config_path)
        board = _require_board(args)
        if args.config_command == "plan":
            print(json.dumps(ConfigService(board).plan(config), indent=2))
        elif args.config_command == "apply":
            print(json.dumps(ConfigService(board).apply(config, actor_id=args.actor), indent=2))
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc


def _run_sync_branch(args: argparse.Namespace) -> None:
    board = _require_board(args)
    current = subprocess.run(
        ["git", "branch", "--show-current"], check=True, text=True, capture_output=True
    ).stdout.strip()
    actor_data = board.authenticate(args.token or "")
    if not actor_data:
        raise SystemExit("valid --token or LOCAL_BOARD_TOKEN required")
    matches = [item for item in board.list_issues() if item["identifier"].lower() in current.lower()]
    for issue in matches:
        board.add_git_link(actor_data["id"], issue["id"], current, "branch")
    print(f"Linked branch {current!r} to {len(matches)} issue(s)")


def _ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    marker = "# Local Board runtime"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in content:
        return
    block = (
        "\n# Local Board runtime\n"
        ".local-board/state/\n"
        ".local-board/backups/\n"
        ".local-board/secrets/\n"
    )
    path.write_text(content.rstrip() + block, encoding="utf-8")


if __name__ == "__main__":
    main()
