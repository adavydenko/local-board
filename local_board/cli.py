from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from .config import ConfigError, ConfigService, default_config, load_config, suggested_key
from .db import Board
from .doctor import run_doctor
from .onboarding import install_onboarding
from .repository import Repository, RepositoryNotFound, resolve_database_path
from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="local-board")
    parser.add_argument("--db", help="database path (defaults to .local-board/state/board.db)")
    parser.add_argument("--config", help="project config path (defaults to .local-board/project.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init"); initialize.add_argument("--force", action="store_true")
    actor = sub.add_parser("actor"); actor.add_argument("name"); actor.add_argument("--kind", choices=("agent", "human"), default="agent")
    web = sub.add_parser("serve"); web.add_argument("--host", default="127.0.0.1"); web.add_argument("--port", type=int, default=8765)
    sync = sub.add_parser("sync-branch"); sync.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN"))
    status = sub.add_parser("status"); status.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor"); doctor.add_argument("--url", default="http://127.0.0.1:8765/mcp"); doctor.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN")); doctor.add_argument("--offline", action="store_true"); doctor.add_argument("--json", action="store_true")
    config_parser = sub.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate"); config_sub.add_parser("plan"); config_sub.add_parser("apply")
    args = parser.parse_args(); board = Board(resolve_database_path(args.db)); board.init()
    try:
        repo = Repository.discover()
        config_path = Path(args.config).resolve() if args.config else repo.config_path
    except RepositoryNotFound:
        repo = None; config_path = Path(args.config or ".local-board/project.toml").resolve()
    if args.command == "init":
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists() and not args.force:
            print(f"Keeping existing {config_path}")
        else:
            name = repo.root.name if repo else Path.cwd().name
            config_path.write_text(default_config(name, suggested_key(name)), encoding="utf-8")
            print(f"Created {config_path}")
        if repo:
            _ensure_gitignore(repo.root)
            installed = install_onboarding(repo.root, force=args.force)
            for path in installed: print(f"Created {path}")
        result = ConfigService(board).apply(load_config(config_path))
        print(f"Initialized {board.path}; applied {len(result['actions'])} configuration action(s)")
    elif args.command == "actor":
        value = board.create_actor(args.name, args.kind); print(f"Actor: {value['name']} ({value['kind']})\nToken (shown once): {value['token']}")
    elif args.command == "serve": serve(board, args.host, args.port)
    elif args.command == "status":
        try:
            repo = Repository.discover()
            value = {"repository": str(repo.root), "git_common_dir": str(repo.git_common_dir), "database": str(board.path), "config": str(config_path), "schema_version": board.schema_version()}
        except RepositoryNotFound:
            value = {"repository": None, "git_common_dir": None, "database": str(board.path), "config": str(config_path), "schema_version": board.schema_version()}
        if args.json: print(json.dumps(value))
        else:
            for key, value_item in value.items(): print(f"{key}: {value_item}")
    elif args.command == "doctor":
        result = run_doctor(board, config_path, url=args.url, token=args.token, online=not args.offline)
        if args.json: print(json.dumps(result, indent=2))
        else:
            icons = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}
            for check in result["checks"]: print(f"[{icons[check['status']]}] {check['name']}: {check['message']}")
        if not result["ok"]: raise SystemExit(1)
    elif args.command == "config":
        try:
            config = load_config(config_path)
            if args.config_command == "validate": print(f"Valid: {config.path} (schema {config.schema_version}, digest {config.digest})")
            elif args.config_command == "plan": print(json.dumps(ConfigService(board).plan(config), indent=2))
            elif args.config_command == "apply": print(json.dumps(ConfigService(board).apply(config), indent=2))
        except ConfigError as exc:
            raise SystemExit(f"configuration error: {exc}") from exc
    elif args.command == "sync-branch":
        current = subprocess.run(["git", "branch", "--show-current"], check=True, text=True, capture_output=True).stdout.strip()
        actor_data = board.authenticate(args.token or "")
        if not actor_data: raise SystemExit("valid --token or LOCAL_BOARD_TOKEN required")
        matches = [i for i in board.list_issues() if i["identifier"].lower() in current.lower()]
        for issue in matches: board.add_related(actor_data["id"], issue["id"], "git_link", link_kind="branch", ref=current)
        print(f"Linked branch {current!r} to {len(matches)} issue(s)")


def _ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    marker = "# Local Board runtime"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in content:
        return
    block = "\n# Local Board runtime\n.local-board/state/\n.local-board/backups/\n.local-board/attachments/\n.local-board/secrets/\n"
    path.write_text(content.rstrip() + block, encoding="utf-8")


if __name__ == "__main__": main()
