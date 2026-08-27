"""Installation and MCP connectivity diagnostics."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__
from .config import ConfigError, ConfigService, load_config
from .db import Board, LEGACY_SCHEMA_VERSIONS, SCHEMA_VERSION


def _check(name: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, **details}


def _mcp_request(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def run_doctor(
    board: Board,
    config_path: str | Path,
    *,
    url: str = "http://127.0.0.1:8765/mcp",
    token: str | None = None,
    online: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    config = None
    try:
        config = load_config(config_path)
        checks.append(_check("config", "pass", f"valid schema {config.schema_version}", digest=config.digest))
    except ConfigError as exc:
        checks.append(_check("config", "fail", str(exc)))

    resolved_config = Path(config_path).resolve()
    if resolved_config.parent.name == ".local-board":
        root = resolved_config.parent.parent
        onboarding = {
            "agent_policy": root / ".local-board" / "AGENT.md",
            "agent_skill": root / ".agents" / "skills" / "local-board" / "SKILL.md",
        }
        for name, path in onboarding.items():
            checks.append(_check(name, "pass" if path.is_file() else "fail", str(path)))
        agents_path = root / "AGENTS.md"
        discoverable = agents_path.is_file() and ".local-board/AGENT.md" in agents_path.read_text(
            encoding="utf-8"
        )
        checks.append(_check(
            "agent_discovery",
            "pass" if discoverable else "warn",
            f"{agents_path} references .local-board/AGENT.md"
            if discoverable else f"merge Local Board instructions into {agents_path}",
        ))

    # Check the schema before touching any table: a pre-0.1.0 database has none
    # of them, and probing it raises a bare `no such table` at the operator.
    version = board.schema_version()
    if version in LEGACY_SCHEMA_VERSIONS:
        checks.append(_check(
            "database_schema", "fail",
            f"schema {version} predates the 0.1.0 board format and cannot be upgraded; "
            f"move {board.path.parent} aside and re-run `local-board init`",
        ))
        return {"ok": False, "checks": checks}
    checks.append(_check(
        "database_schema",
        "pass" if version == SCHEMA_VERSION else "fail",
        f"schema {version}; supported {SCHEMA_VERSION}",
    ))

    try:
        info = board.get_board()
        checks.append(_check("board", "pass", f"{info['prefix']}: {info['name']}"))
    except (KeyError, sqlite3.OperationalError) as exc:
        checks.append(_check("board", "fail", str(exc).strip("'")))
    with board.connect() as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
    checks.append(_check("database_integrity", "pass" if integrity == "ok" else "fail", integrity))

    server_file = board.path.parent / "server.json"
    if server_file.exists():
        # A clean shutdown removes this file, so a dead PID here means the server
        # died uncleanly: point at the crash log instead of leaving a silent mystery.
        try:
            pid = int(json.loads(server_file.read_text(encoding="utf-8")).get("pid", 0))
            os.kill(pid, 0)
            checks.append(_check("server", "pass", f"running (pid {pid})"))
        except (ProcessLookupError, ValueError):
            checks.append(_check(
                "server", "warn",
                f"stale {server_file.name}: recorded server is not running; it likely died "
                "uncleanly — see server-crash.log and remove the stale file",
            ))
        except PermissionError:
            checks.append(_check("server", "pass", "running (pid owned by another user)"))
    checks.append(_check(
        "foreign_keys",
        "pass" if not foreign_keys else "fail",
        "ok" if not foreign_keys else f"{len(foreign_keys)} violation(s)",
    ))

    if config is not None:
        try:
            plan = ConfigService(board).plan(config)
            checks.append(_check(
                "config_drift",
                "warn" if plan["changed"] else "pass",
                f"{len(plan['actions'])} pending action(s)",
                actions=plan["actions"],
            ))
        except (KeyError, ValueError) as exc:
            checks.append(_check("config_drift", "fail", str(exc).strip("'")))

    if online:
        if not token:
            checks.append(_check(
                "mcp_auth", "fail", "LOCAL_BOARD_TOKEN or --token is required for online checks",
            ))
        else:
            try:
                initialized = _mcp_request(url, token, {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "local-board-doctor", "version": __version__},
                    },
                })
                server = initialized["result"]["serverInfo"]
                checks.append(_check("mcp_initialize", "pass", f"{server['name']} {server['version']}"))
                tools = _mcp_request(url, token, {
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
                })["result"]["tools"]
                checks.append(_check(
                    "mcp_tools", "pass" if tools else "fail", f"{len(tools)} tool(s) available",
                ))
            except HTTPError as exc:
                checks.append(_check("mcp_connectivity", "fail", f"HTTP {exc.code} from {url}"))
            except (URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
                checks.append(_check("mcp_connectivity", "fail", f"cannot validate {url}: {exc}"))
    else:
        checks.append(_check("mcp_connectivity", "skip", "online checks disabled"))

    return {"ok": not any(item["status"] == "fail" for item in checks), "checks": checks}
