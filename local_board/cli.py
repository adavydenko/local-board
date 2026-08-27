"""The `local-board` command line.

argparse does the parsing; `usage` owns every page the operator reads and
`ui` owns how it is painted. Three rules hold across all commands:

  * a bare or unknown invocation teaches instead of erroring at the user,
  * `--json` is global — anything a human can read, a harness can parse,
  * failures name the next command to run in a `hint:` line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, ui, usage
from .backup import create_backup, restore_backup
from .config import ConfigError, ConfigService, default_config, load_config, suggested_prefix
from .db import Board
from .doctor import run_doctor
from .errors import describe
from .onboarding import install_onboarding
from .repository import Repository, RepositoryNotFound, resolve_database_path
from . import reset as reset_module
from .web import serve

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

# The next command to run, keyed by the error contract in errors.py. Every
# failure the CLI can name gets a hint; `internal` deliberately gets none.
_HINTS = {
    "conflict": "re-read current state with `local-board status`, then retry",
    "unauthorized": "the actor lacks the required role; mint one with `local-board actor <name> --role admin`",
    "retryable": "the database was busy; retry the command",
    "not_found": "check the identifier with `local-board status`",
}


class _HelpAction(argparse.Action):
    """Print our page and stop. Fires while parsing, so `--help` still works
    on a command with required arguments the user has not supplied yet."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs) -> None:
        super().__init__(option_strings, dest=dest, default=argparse.SUPPRESS,
                         nargs=0, help=argparse.SUPPRESS)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        parser.print_help()
        parser.exit(EXIT_OK)


class Parser(argparse.ArgumentParser):
    """An argparse parser that prints our pages and our error shape."""

    def __init__(self, *args, command: str | None = None, **kwargs) -> None:
        super().__init__(*args, add_help=False, **kwargs)
        self.command_name = command
        self.add_argument("-h", "--help", action=_HelpAction)

    def format_help(self) -> str:  # pragma: no cover - exercised through print_help
        import io

        buffer = io.StringIO()
        if self.command_name:
            usage.command_help(self.command_name, buffer)
        else:
            usage.overview(buffer)
        return buffer.getvalue()

    def error(self, message: str) -> None:
        """Replace `usage: ... error: ...` with a hint that names the fix."""
        if "invalid choice" in message:
            unknown = message.split("'")[1] if "'" in message else ""
            suggestions = usage.did_you_mean(unknown)
            hints = [f"did you mean `{suggestions[0]}`?"] if suggestions else []
            hints.append("run `local-board --help` to see every command")
            ui.error(f"unknown command `{unknown}`", *hints)
        else:
            scope = f"local-board {self.command_name}" if self.command_name else "local-board"
            ui.error(message, f"run `{scope} --help` for the accepted arguments")
        raise SystemExit(EXIT_USAGE)


def _add_global_options(parser: argparse.ArgumentParser, *, nested: bool = False) -> None:
    """Global flags work before or after the command, as in uv and cargo.

    Nested copies suppress their defaults so a flag given before the command is
    not silently reset to False when the subparser re-parses the same name.
    """
    default = argparse.SUPPRESS if nested else None
    parser.add_argument("--db", default=default, metavar="PATH",
                        help="database path (default .local-board/state/board.db)")
    parser.add_argument("--config", default=default, metavar="PATH",
                        help="project config path (default .local-board/project.toml)")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if nested else False,
                        help="emit machine-readable JSON")
    parser.add_argument("--color", "--colour", dest="color", choices=("auto", "always", "never"),
                        default=argparse.SUPPRESS if nested else "auto", metavar="WHEN",
                        help="when to colourise output")


def build_parser() -> Parser:
    parser = Parser(prog="local-board")
    # -v is version here rather than verbosity: it is what people type first,
    # and Local Board has no verbosity levels to spend the letter on.
    parser.add_argument("-V", "-v", "--version", action="version",
                        version=f"local-board {__version__}")
    _add_global_options(parser)
    sub = parser.add_subparsers(dest="command", required=False, metavar="<command>",
                                parser_class=Parser)

    def command(name: str) -> Parser:
        child = sub.add_parser(name, command=name)
        _add_global_options(child, nested=True)
        return child

    initialize = command("init")
    initialize.add_argument("--force", action="store_true")

    actor = command("actor")
    actor.add_argument("name")
    actor.add_argument("--kind", choices=("agent", "human"), default="agent")
    actor.add_argument("--role", choices=("admin", "member", "viewer"))

    reset_parser = command("reset")
    reset_parser.add_argument("--all", action="store_true", dest="everything")
    reset_parser.add_argument("--purge", action="store_true")
    reset_parser.add_argument("--force", action="store_true")

    web = command("serve")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    command("status")

    backup = command("backup")
    backup.add_argument("path", nargs="?")

    restore = command("restore")
    restore.add_argument("path")
    restore.add_argument("--force", action="store_true")

    doctor = command("doctor")
    doctor.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    doctor.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN"))
    doctor.add_argument("--offline", action="store_true")

    config_parser = command("config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=False,
                                              metavar="<subcommand>", parser_class=Parser)
    for name in ("validate", "plan"):
        child = config_sub.add_parser(name, command="config")
        _add_global_options(child, nested=True)
    apply_config = config_sub.add_parser("apply", command="config")
    _add_global_options(apply_config, nested=True)
    apply_config.add_argument("--actor", type=int)

    help_parser = command("help")
    help_parser.add_argument("topic", nargs="?")

    return parser


# -- entry point --------------------------------------------------------------

def _early_colour(argv: list[str]) -> str:
    """Read --color before argparse runs: `--help` and usage errors print during
    parsing, and they should be painted the same way as everything else."""
    if "--json" in argv:
        return "never"
    when = "auto"
    for index, token in enumerate(argv):
        if token in ("--color", "--colour") and index + 1 < len(argv):
            when = argv[index + 1]
        elif token.startswith(("--color=", "--colour=")):
            when = token.split("=", 1)[1]
    return when if when in ("auto", "always", "never") else "auto"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ui.configure(_early_colour(argv))
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except BrokenPipeError:
        return _silence_broken_pipe()

    ui.configure("never" if getattr(args, "json", False) else getattr(args, "color", "auto"))

    try:
        if args.command is None:
            usage.overview(sys.stdout)
            return EXIT_OK
        if args.command == "help":
            return _run_help(args.topic)
    except BrokenPipeError:
        return _silence_broken_pipe()

    config_path = _config_path(args)
    try:
        _dispatch(args, config_path)
    except SystemExit:
        raise
    except BrokenPipeError:
        return _silence_broken_pipe()
    except Exception as exc:
        _report_error(args, exc)
        return EXIT_FAILURE
    return EXIT_OK


def _silence_broken_pipe() -> int:
    """`local-board --help | head` closes the pipe early; that is the reader's
    choice, not an error. Point stdout at devnull so the interpreter's shutdown
    flush cannot raise a second time on the way out."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError:
        pass
    return EXIT_OK


def _run_help(topic: str | None) -> int:
    if topic is None:
        usage.overview(sys.stdout)
        return EXIT_OK
    if topic not in usage.BY_NAME:
        suggestions = usage.did_you_mean(topic)
        hints = [f"did you mean `{suggestions[0]}`?"] if suggestions else []
        hints.append("run `local-board --help` to see every command")
        ui.error(f"unknown command `{topic}`", *hints)
        return EXIT_USAGE
    usage.command_help(topic, sys.stdout)
    return EXIT_OK


def _dispatch(args: argparse.Namespace, config_path: Path) -> None:
    if args.command == "init":
        _run_init(args, config_path)
    elif args.command == "actor":
        _run_actor(args)
    elif args.command == "reset":
        _run_reset(args, config_path)
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


def _hint_for(code: str, message: str) -> str | None:
    if message.endswith("already exists"):
        return "choose a different name, or reuse the existing record"
    return _HINTS.get(code)


def _report_error(args: argparse.Namespace, exc: Exception) -> None:
    _, code, message, retryable = describe(exc)
    if code == "internal":
        message = str(exc)
    if getattr(args, "json", False):
        print(json.dumps({"error": {"code": code, "message": message, "retryable": retryable}}))
    else:
        hint = _hint_for(code, message)
        ui.error(message, *((hint,) if hint else ()))
    if code == "internal" and os.environ.get("LOCAL_BOARD_DEBUG"):
        raise exc


# -- shared resolution --------------------------------------------------------

def _config_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config).expanduser().resolve()
    if os.environ.get("LOCAL_BOARD_CONFIG"):
        return Path(os.environ["LOCAL_BOARD_CONFIG"]).expanduser().resolve()
    try:
        return Repository.discover().config_path
    except RepositoryNotFound:
        return Path(".local-board/project.toml").resolve()


def _require_board(args: argparse.Namespace) -> Board:
    db_path = resolve_database_path(getattr(args, "db", None))
    if not db_path.exists():
        ui.error(
            f"no board at {_display(db_path)}",
            "run `local-board init` to create one, or pass --db to point at an existing board",
        )
        raise SystemExit(EXIT_FAILURE)
    return Board(db_path)


def _display(path: Path) -> str:
    """Show repository-relative paths; they are shorter and stable in docs."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _summarize(paths: list[Path], limit: int = 3) -> str:
    """Name the first few onboarding files and count the rest: init writes many."""
    if not paths:
        return ui.theme.dim("skipped (no Git repository)")
    shown = ", ".join(_display(path) for path in paths[:limit])
    extra = len(paths) - limit
    return f"{shown}{ui.theme.dim(f', +{extra} more')}" if extra > 0 else shown


def _emit(args: argparse.Namespace, payload: dict) -> bool:
    """Print JSON and report whether the human rendering should be skipped."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return True
    return False


# -- commands -----------------------------------------------------------------

def _run_init(args: argparse.Namespace, config_path: Path) -> None:
    try:
        repo = Repository.discover()
    except RepositoryNotFound:
        repo = None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not args.force:
        config_state = "kept"
    else:
        name = repo.root.name if repo else Path.cwd().name
        config_path.write_text(default_config(name, suggested_prefix(name)), encoding="utf-8")
        config_state = "created"
    onboarding: list[Path] = []
    if repo:
        _ensure_gitignore(repo.root)
        onboarding = list(install_onboarding(repo.root, force=args.force))
    board = Board(resolve_database_path(getattr(args, "db", None)))
    board.init()
    result = ConfigService(board).apply(load_config(config_path))

    payload = {
        "repository": str(repo.root) if repo else None,
        "config": {"path": str(config_path), "state": config_state},
        "database": str(board.path),
        "onboarding": [str(path) for path in onboarding],
        "actions": len(result["actions"]),
    }
    if _emit(args, payload):
        return

    root = repo.root if repo else Path.cwd()
    ui.heading(f"Initialized Local Board in {root}")
    print()
    ui.fields([
        ("config", f"{_display(config_path)} {ui.theme.dim(f'({config_state})')}"),
        ("database", _display(board.path)),
        ("onboarding", _summarize(onboarding)),
        ("config actions", f"{len(result['actions'])} applied"),
    ])
    print()
    ui.heading("Next")
    steps = [
        "git add .local-board/project.toml .local-board/AGENT.md .agents AGENTS.md .gitignore",
        "local-board actor <your-name> --kind human",
        "local-board serve",
    ]
    for index, step in enumerate(steps, start=1):
        print(f"  {ui.theme.dim(str(index))}  {step}")


def _run_reset(args: argparse.Namespace, config_path: Path) -> None:
    db_path = resolve_database_path(getattr(args, "db", None))
    state_dir = db_path.parent
    try:
        root = Repository.discover().root
    except RepositoryNotFound:
        root = Path.cwd()

    pid = reset_module.server_pid(state_dir)
    if pid is not None:
        ui.error(f"a Local Board server is still running (pid {pid})",
                 "stop it first, then re-run `local-board reset`")
        raise SystemExit(EXIT_FAILURE)

    removals = reset_module.plan(root, state_dir, config_path,
                                 everything=args.everything, purge=args.purge)
    if not removals:
        if _emit(args, {"planned": [], "removed": [], "forced": args.force}):
            return
        print(ui.theme.dim("nothing to remove: this repository has no Local Board state"))
        return

    if not args.force:
        if _emit(args, {"planned": [item.as_dict(root) for item in removals], "removed": []}):
            raise SystemExit(EXIT_FAILURE)
        ui.heading("reset would remove")
        print()
        _print_removals(removals)
        print()
        ui.error("nothing was removed", "re-run with --force to carry out the plan")
        raise SystemExit(EXIT_FAILURE)

    done = reset_module.apply(removals)
    if _emit(args, {"planned": [item.as_dict(root) for item in removals], "removed": done}):
        return
    ui.heading("Reset")
    print()
    _print_removals(removals)
    moved = [item for item in done if item["action"] == "moved"]
    print()
    if moved:
        print(ui.theme.dim("moved aside (delete when you are sure):"))
        for item in moved:
            print(f"  {_display(Path(item['destination']))}")
        print()
    ui.heading("Next")
    print("  local-board init")


def _print_removals(removals: list) -> None:
    width = max(len(item.kind) for item in removals)
    verbs = {"move": "move", "delete": "remove", "edit": "edit", "keep": "keep"}
    verb_width = max(len(verbs[item.action]) for item in removals)
    for item in removals:
        note = f" {ui.theme.dim('— ' + item.note)}" if item.note else ""
        print(f"  {ui.theme.dim(verbs[item.action].ljust(verb_width))}  {item.kind.ljust(width)}  "
              f"{_display(item.path)}{note}")


def _run_actor(args: argparse.Namespace) -> None:
    board = _require_board(args)
    value = board.create_actor(args.name, args.kind, args.role)
    if _emit(args, value):
        return
    ui.heading(f"Created actor {value['name']}")
    print()
    ui.fields([
        ("kind", value["kind"]),
        ("role", value["role"]),
        ("token", value["token"]),
    ])
    print()
    ui.warn("the token is shown once and cannot be recovered",
            "export it as LOCAL_BOARD_TOKEN; never commit it")


def _run_status(args: argparse.Namespace, config_path: Path) -> None:
    db_path = resolve_database_path(getattr(args, "db", None))
    try:
        repo = Repository.discover()
        value = {"repository": str(repo.root), "git_common_dir": str(repo.git_common_dir)}
    except RepositoryNotFound:
        repo = None
        value = {"repository": None, "git_common_dir": None}
    value["config"] = str(config_path)
    if db_path.exists():
        value["database"] = str(db_path)
        value["schema_version"] = Board(db_path).schema_version()
    else:
        value["database"] = "not initialized"
    value["server"] = _server_state(db_path)
    if _emit(args, value):
        return

    ui.heading(f"Local Board {__version__}")
    print()
    rows = [("repository", str(repo.root) if repo else ui.theme.dim("not a Git repository"))]
    if config_path.exists():
        rows.append(("config", _display(config_path)))
    else:
        rows.append(("config", f"{_display(config_path)} {ui.theme.dim('(missing)')}"))
    if db_path.exists():
        schema = ui.theme.dim(f"(schema {value['schema_version']})")
        rows.append(("database", f"{_display(db_path)} {schema}"))
    else:
        rows.append(("database", ui.theme.dim("not initialized")))
    server = value["server"]
    if server:
        rows.append(("server", f"{server['url']} {ui.theme.dim('(pid ' + str(server['pid']) + ')')}"))
    else:
        rows.append(("server", ui.theme.dim("stopped")))
    ui.fields(rows)
    if not db_path.exists():
        print()
        ui.warn("this repository has no board yet", "run `local-board init`")


def _server_state(db_path: Path) -> dict | None:
    path = db_path.parent / "server.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_backup(args: argparse.Namespace) -> None:
    board = _require_board(args)
    destination = (
        Path(args.path).resolve()
        if args.path
        else board.path.parent.parent / "backups" / f"board-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    )
    manifest = create_backup(board, destination)
    if _emit(args, manifest):
        return
    ui.heading("Backup written")
    print()
    ui.fields([
        ("path", _display(Path(manifest["path"]))),
        ("size", f"{manifest['size']} bytes"),
        ("schema", str(manifest["schema_version"])),
        ("sha256", manifest["sha256"]),
    ])


def _run_restore(args: argparse.Namespace) -> None:
    if not args.force:
        ui.error("restore replaces the current board state",
                 "re-run with --force once you are sure, and stop `local-board serve` first")
        raise SystemExit(EXIT_FAILURE)
    board = _require_board(args)
    safety = board.path.parent.parent / "backups" / f"pre-restore-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    if board.path.exists():
        create_backup(board, safety)
    result = restore_backup(board, args.path)
    if _emit(args, result):
        return
    ui.heading("Board state restored")
    print()
    ui.fields([
        ("from", _display(Path(result["source"]))),
        ("into", _display(Path(result["path"]))),
        ("safety copy", _display(safety)),
        ("sha256", result["sha256"]),
    ])


def _run_doctor_command(args: argparse.Namespace, config_path: Path) -> None:
    db_path = resolve_database_path(getattr(args, "db", None))
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
    if _emit(args, result):
        if not result["ok"]:
            raise SystemExit(EXIT_FAILURE)
        return

    checks = result["checks"]
    width = max(len(check["name"]) for check in checks)
    ui.heading("Diagnostics")
    print()
    for check in checks:
        ui.check(check["status"], check["name"], check["message"], width=width)
    tally = {status: sum(1 for c in checks if c["status"] == status)
             for status in ("pass", "fail", "warn", "skip")}
    print()
    summary = ", ".join(f"{count} {label}" for label, count in
                        (("passed", tally["pass"]), ("failed", tally["fail"]),
                         ("warned", tally["warn"]), ("skipped", tally["skip"])) if count)
    print(ui.theme.dim(summary))
    if not result["ok"]:
        print()
        ui.error("diagnostics failed", "fix the failing checks above, then re-run `local-board doctor`")
        raise SystemExit(EXIT_FAILURE)


def _run_config(args: argparse.Namespace, config_path: Path) -> None:
    if args.config_command is None:
        usage.command_help("config", sys.stdout)
        raise SystemExit(EXIT_USAGE)
    try:
        config = load_config(config_path)
        if args.config_command == "validate":
            payload = {"path": str(config.path), "schema_version": config.schema_version,
                       "digest": config.digest, "valid": True}
            if _emit(args, payload):
                return
            ui.heading("Config is valid")
            print()
            ui.fields([
                ("path", _display(config.path)),
                ("schema", str(config.schema_version)),
                ("digest", config.digest),
            ])
            return
        board = _require_board(args)
        service = ConfigService(board)
        if args.config_command == "plan":
            result = service.plan(config)
            verb, heading = "would apply", "Planned configuration changes"
        else:
            result = service.apply(config, actor_id=args.actor)
            verb, heading = "applied", "Configuration applied"
        if _emit(args, result):
            return
        actions = result.get("actions", [])
        ui.heading(heading)
        print()
        if not actions:
            print(ui.theme.dim("  the board already matches the config"))
            return
        for action in actions:
            print(f"  {ui.theme.cyan('•')} {json.dumps(action) if not isinstance(action, str) else action}")
        print()
        print(ui.theme.dim(f"{len(actions)} action(s) {verb}"))
    except ConfigError as exc:
        ui.error(f"configuration error: {exc}",
                 "run `local-board config validate` after fixing .local-board/project.toml")
        raise SystemExit(EXIT_FAILURE) from exc


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
    raise SystemExit(main())
