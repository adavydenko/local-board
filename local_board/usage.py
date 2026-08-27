"""The command table and the help pages rendered from it.

argparse builds the parser; this module owns what the user reads. Both are
driven by the same table, so a new command cannot appear in one and be missing
from the other. The layout follows the grouped-overview convention of modern
CLIs (uv, cargo, bun): bare invocation shows the map, not a usage error.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import IO, Sequence

from . import __version__, ui

TAGLINE = "a planning board that lives in your repository"
DOCS_URL = "https://github.com/adavydenko/local-board"


@dataclass(frozen=True)
class Option:
    flags: str
    help: str
    env: str = ""


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    usage: tuple[str, ...] = ()
    description: str = ""
    options: tuple[Option, ...] = ()
    examples: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    subcommands: tuple[tuple[str, str], ...] = ()


GLOBAL_OPTIONS: tuple[Option, ...] = (
    Option("    --db <path>", "Database path", "LOCAL_BOARD_DB"),
    Option("    --config <path>", "Project config path", "LOCAL_BOARD_CONFIG"),
    Option("    --json", "Emit machine-readable JSON"),
    Option("    --color <when>", "auto, always, or never", "NO_COLOR"),
    Option("-h, --help", "Show help"),
    Option("-V, --version", "Show the version"),
)

COMMANDS: tuple[Command, ...] = (
    Command(
        "init", "Set up",
        "Create the board, config, and agent onboarding files",
        usage=("local-board init [--force]",),
        description=(
            "Writes .local-board/project.toml, creates the SQLite board, installs the\n"
            "agent skill and AGENTS.md bridge, and extends .gitignore. Safe to re-run:\n"
            "existing files are kept unless --force is given. Root instructions are\n"
            "never overwritten, not even with --force."
        ),
        options=(Option("    --force", "Overwrite the project config and onboarding files"),),
        examples=("local-board init", "local-board init --force"),
        next_steps=("local-board actor <your-name> --kind human", "local-board serve"),
    ),
    Command(
        "actor", "Set up",
        "Create an actor and print its token once",
        usage=("local-board actor <name> [--kind agent|human] [--role admin|member|viewer]",),
        description=(
            "Tokens are shown exactly once and never stored in readable form. Pass --json\n"
            "when an orchestrator needs to capture the token; keep it out of tracked files."
        ),
        options=(
            Option("    --kind <kind>", "agent (default) or human"),
            Option("    --role <role>", "admin, member, or viewer"),
        ),
        examples=(
            "local-board actor alice --kind human",
            "local-board actor coding-agent --kind agent --json",
        ),
    ),
    Command(
        "serve", "Run",
        "Serve the web UI and the MCP endpoint",
        usage=("local-board serve [--host <host>] [--port <port>]",),
        description=(
            "One server owns the board for the whole repository, including every linked\n"
            "worktree. Runs in the foreground; Ctrl+C shuts it down and clears server.json."
        ),
        options=(
            Option("    --host <host>", "Bind address (default 127.0.0.1)"),
            Option("    --port <port>", "Port (default 8765)"),
        ),
        examples=("local-board serve", "local-board serve --port 9000"),
    ),
    Command(
        "status", "Inspect",
        "Show repository, config, database, and server state",
        usage=("local-board status [--json]",),
        examples=("local-board status", "local-board status --json"),
    ),
    Command(
        "doctor", "Inspect",
        "Diagnose config, database, onboarding, and server health",
        usage=("local-board doctor [--offline] [--url <url>] [--token <token>]",),
        description=(
            "Exits non-zero when a check fails, so it composes in CI. Online checks need a\n"
            "token: pass --token or export LOCAL_BOARD_TOKEN."
        ),
        options=(
            Option("    --offline", "Skip checks that need a running server"),
            Option("    --url <url>", "MCP endpoint (default http://127.0.0.1:8765/mcp)"),
            Option("    --token <token>", "Bearer token for online checks", "LOCAL_BOARD_TOKEN"),
        ),
        examples=("local-board doctor --offline", "local-board doctor --json"),
    ),
    Command(
        "config", "Configure",
        "Validate, plan, and apply .local-board/project.toml",
        usage=("local-board config <validate|plan|apply> [--actor <id>]",),
        description=(
            "Apply is additive and atomic: entities omitted from TOML are never deleted,\n"
            "and every effective apply is recorded with its digest and diff."
        ),
        subcommands=(
            ("validate", "Check syntax and semantics without touching the board"),
            ("plan", "Show the actions apply would take"),
            ("apply", "Reconcile the board with the config"),
        ),
        options=(Option("    --actor <id>", "Attribute the apply to an actor (apply only)"),),
        examples=("local-board config validate", "local-board config plan"),
    ),
    Command(
        "backup", "Data",
        "Write a checksummed snapshot of board state",
        usage=("local-board backup [<path>]",),
        description=(
            "Takes a consistent online snapshot with a JSON manifest carrying the format,\n"
            "schema version, size, and SHA-256. Defaults to .local-board/backups/."
        ),
        examples=("local-board backup", "local-board backup /tmp/board.db --json"),
    ),
    Command(
        "restore", "Data",
        "Replace board state from a snapshot",
        usage=("local-board restore <path> --force",),
        description=(
            "Validates checksum, SQLite integrity, and required tables before replacing\n"
            "state atomically, keeping a pre-restore snapshot. Stop `serve` first."
        ),
        options=(Option("    --force", "Required: restore discards current state"),),
        examples=("local-board restore .local-board/backups/board-20260818T120000Z.db --force",),
    ),
    Command(
        "help", "", "Show help for a command",
        usage=("local-board help [<command>]",),
        examples=("local-board help serve",),
    ),
)

BY_NAME = {command.name: command for command in COMMANDS}
GROUP_ORDER = ("Set up", "Run", "Inspect", "Configure", "Data")


def _option_lines(options: Sequence[Option], width: int) -> list[str]:
    lines = []
    for option in options:
        tail = ui.theme.dim(f"  [env: {option.env}]") if option.env else ""
        lines.append(f"  {option.flags.ljust(width)}  {option.help}{tail}")
    return lines


def _options_block(title: str, options: Sequence[Option], out: IO[str]) -> None:
    if not options:
        return
    width = max(len(option.flags) for option in options)
    print(file=out)
    ui.heading(title, stream=out)
    for line in _option_lines(options, width):
        print(line, file=out)


def overview(out: IO[str]) -> None:
    """The page a bare `local-board` prints: what exists, grouped by intent."""
    print(f"{ui.theme.bold('Local Board')} {ui.theme.dim(__version__)} — {TAGLINE}", file=out)
    print(file=out)
    ui.heading("Usage", stream=out)
    print(f"  local-board {ui.theme.cyan('<command>')} [options]", file=out)

    width = max(len(command.name) for command in COMMANDS)
    for group in GROUP_ORDER:
        members = [command for command in COMMANDS if command.group == group]
        if not members:
            continue
        print(file=out)
        ui.heading(group, stream=out)
        ui.listing(((c.name, c.summary) for c in members), width=width, stream=out)

    _options_block("Options", GLOBAL_OPTIONS, out)

    print(file=out)
    ui.heading("Getting started", stream=out)
    ui.listing(
        (
            ("local-board init", "set up the board in this repository"),
            ("local-board serve", "start the web UI and MCP endpoint"),
            ("local-board doctor", "check that agents can reach it"),
        ),
        width=len("local-board doctor"),
        stream=out,
    )
    print(file=out)
    print(f"Run {ui.theme.cyan('local-board help <command>')} for details on a command.", file=out)
    print(ui.theme.dim(f"Docs: {DOCS_URL}"), file=out)


def command_help(name: str, out: IO[str]) -> None:
    command = BY_NAME[name]
    print(command.summary, file=out)
    if command.description:
        print(file=out)
        print(ui.theme.dim(command.description), file=out)

    print(file=out)
    ui.heading("Usage", stream=out)
    for line in command.usage:
        print(f"  {line}", file=out)

    if command.subcommands:
        print(file=out)
        ui.heading("Subcommands", stream=out)
        width = max(len(sub) for sub, _ in command.subcommands)
        ui.listing(command.subcommands, width=width, stream=out)

    options = command.options + (Option("-h, --help", "Show this help"),)
    _options_block("Options", options, out)
    _options_block("Global options", GLOBAL_OPTIONS[:4], out)

    if command.examples:
        print(file=out)
        ui.heading("Examples", stream=out)
        for example in command.examples:
            print(f"  {example}", file=out)

    if command.next_steps:
        print(file=out)
        ui.heading("Next", stream=out)
        for step in command.next_steps:
            print(f"  {step}", file=out)


def did_you_mean(name: str) -> list[str]:
    return difflib.get_close_matches(name, list(BY_NAME), n=3, cutoff=0.5)
