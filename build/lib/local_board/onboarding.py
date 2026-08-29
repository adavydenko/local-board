"""Install repository-local instructions without overwriting local edits."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


TEMPLATES = {
    Path("AGENTS.md"): "local-board-agents-bridge.md",
    Path(".local-board/AGENT.md"): "local-board-agent.md",
    Path(".agents/skills/local-board/SKILL.md"): "local-board-skill.md",
    Path(".agents/skills/local-board/references/tools.md"): "local-board-tools.md",
    Path(".agents/skills/local-board/agents/openai.yaml"): "local-board-openai.yaml",
}


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
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(templates.joinpath(template_name).read_text(encoding="utf-8"), encoding="utf-8")
        created.append(destination)
    return created
