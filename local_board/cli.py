from __future__ import annotations

import argparse
import os
import subprocess

from .db import Board
from .mcp import serve_stdio
from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="local-board")
    parser.add_argument("--db", default=os.environ.get("LOCAL_BOARD_DB", ".local-board/board.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    actor = sub.add_parser("actor"); actor.add_argument("name"); actor.add_argument("--kind", choices=("agent", "human"), default="agent")
    web = sub.add_parser("serve"); web.add_argument("--host", default="127.0.0.1"); web.add_argument("--port", type=int, default=8765)
    sub.add_parser("mcp")
    sync = sub.add_parser("sync-branch"); sync.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN"))
    args = parser.parse_args(); board = Board(args.db); board.init()
    if args.command == "init": print(f"Initialized {board.path}")
    elif args.command == "actor":
        value = board.create_actor(args.name, args.kind); print(f"Actor: {value['name']} ({value['kind']})\nToken (shown once): {value['token']}")
    elif args.command == "serve": serve(board, args.host, args.port)
    elif args.command == "mcp": serve_stdio(board, os.environ.get("LOCAL_BOARD_TOKEN", ""))
    elif args.command == "sync-branch":
        current = subprocess.run(["git", "branch", "--show-current"], check=True, text=True, capture_output=True).stdout.strip()
        actor_data = board.authenticate(args.token or "")
        if not actor_data: raise SystemExit("valid --token or LOCAL_BOARD_TOKEN required")
        matches = [i for i in board.list_issues() if i["identifier"].lower() in current.lower()]
        for issue in matches: board.add_related(actor_data["id"], issue["id"], "git_link", link_kind="branch", ref=current)
        print(f"Linked branch {current!r} to {len(matches)} issue(s)")


if __name__ == "__main__": main()
