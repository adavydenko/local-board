"""Install repository-local instructions without overwriting local edits."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from . import mcp


# None marks content rendered from the MCP tool catalog instead of a static file,
# so the cheat-sheet cannot drift from what tools/list actually serves.
TEMPLATES: dict[Path, str | None] = {
    Path("AGENTS.md"): "local-board-agents-bridge.md",
    Path(".local-board/AGENT.md"): "local-board-agent.md",
    Path(".agents/skills/local-board/SKILL.md"): "local-board-skill.md",
    Path(".agents/skills/local-board/references/tools.md"): None,
    Path(".agents/skills/local-board/agents/openai.yaml"): "local-board-openai.yaml",
}

_TOOLS_PREAMBLE = """\
# Tool signatures

Generated from the MCP tool catalog (`local_board/mcp.py`) — the same source `tools/list` serves.
Do not edit by hand; `local-board init --force` regenerates it.

`issue` = `APP-12` style identifier; `milestone`/`label`/`actor` accept key, name, or id. `expected_revision` must come from your latest read or mutation response.

Required params are bare; optional params are marked `?`. Trailing marker: `rev+` advances the issue's revision, `rev=` does not.

A conflict error names the current revision — use it directly instead of re-reading.
"""


def _signature(item: dict) -> str:
    schema = item["inputSchema"]
    required = schema.get("required", [])
    names = list(schema.get("properties", {}))
    ordered = [name for name in names if name in required] + [
        f"{name}?" for name in names if name not in required
    ]
    return f"`{item['name']}({', '.join(ordered)})`"


def _line(item: dict) -> str:
    line = f"- {_signature(item)}"
    marker = mcp.TOOL_REV.get(item["name"])
    if marker:
        line += f" rev{marker}"
    note = mcp.TOOL_NOTES.get(item["name"])
    if note:
        line += f" — {note}"
    return line


def render_tools_reference() -> str:
    sections = [
        ("READ", mcp.TOOLS_READ),
        ("WRITE (member)", mcp.TOOLS_WRITE),
        ("ADMIN-ONLY", mcp.TOOLS_CORRECTION + mcp.TOOLS_ADMIN),
    ]
    blocks = [_TOOLS_PREAMBLE]
    for title, tools in sections:
        blocks.append(f"{title}:\n" + "\n".join(_line(item) for item in tools) + "\n")
    return "\n".join(blocks)


def install_onboarding(root: Path, *, force: bool = False) -> list[Path]:
    created: list[Path] = []
    templates = files("local_board").joinpath("templates")
    for relative, template_name in TEMPLATES.items():
        destination = root / relative
        # Root instructions may contain unrelated human policy. Never replace them,
        # even when --force refreshes Local Board-owned templates.
        if relative == Path("AGENTS.md") and destination.exists():
            continue
        if destination.exists() and not force:
            continue
        if template_name is None:
            content = render_tools_reference()
        else:
            content = templates.joinpath(template_name).read_text(encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        created.append(destination)
    return created
