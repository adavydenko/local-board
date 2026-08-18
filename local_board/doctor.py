"""Installation and MCP connectivity diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import ConfigError, ConfigService, load_config
from .db import Board, SCHEMA_VERSION


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

    version = board.schema_version()
    checks.append(_check("database_schema", "pass" if version == SCHEMA_VERSION else "fail", f"schema {version}; supported {SCHEMA_VERSION}"))
    with board.connect() as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
    checks.append(_check("database_integrity", "pass" if integrity == "ok" else "fail", integrity))
    checks.append(_check("foreign_keys", "pass" if not foreign_keys else "fail", "ok" if not foreign_keys else f"{len(foreign_keys)} violation(s)"))

    if config is not None:
        try:
            plan = ConfigService(board).plan(config)
            checks.append(_check("config_drift", "warn" if plan["changed"] else "pass", f"{len(plan['actions'])} pending action(s)", actions=plan["actions"]))
        except (KeyError, ValueError) as exc:
            checks.append(_check("config_drift", "fail", str(exc).strip("'")))

    if online:
        if not token:
            checks.append(_check("mcp_auth", "fail", "LOCAL_BOARD_TOKEN or --token is required for online checks"))
        else:
            try:
                initialized = _mcp_request(url, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "local-board-doctor", "version": "0.1.0"}}})
                server = initialized["result"]["serverInfo"]
                checks.append(_check("mcp_initialize", "pass", f"{server['name']} {server['version']}"))
                tools = _mcp_request(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})["result"]["tools"]
                checks.append(_check("mcp_tools", "pass" if tools else "fail", f"{len(tools)} tool(s) available"))
            except HTTPError as exc:
                checks.append(_check("mcp_connectivity", "fail", f"HTTP {exc.code} from {url}"))
            except (URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
                checks.append(_check("mcp_connectivity", "fail", f"cannot validate {url}: {exc}"))
    else:
        checks.append(_check("mcp_connectivity", "skip", "online checks disabled"))

    return {"ok": not any(item["status"] == "fail" for item in checks), "checks": checks}
