# Human guide

## Install into an existing repository

Local Board requires Python 3.11 or newer. Install the tool in an isolated environment; while developing locally, point pipx at this checkout:

```bash
pipx install /path/to/local-board
cd /path/to/product-repository
local-board init
```

`init` creates and applies `.local-board/project.toml`, installs repository-local agent instructions and the Local Board skill, adds runtime paths to `.gitignore`, and creates `.local-board/state/board.db`. Review and commit only the tracked configuration and instruction files:

Agent harnesses do not universally discover `.local-board/AGENT.md`, so `init` creates a root `AGENTS.md` discovery bridge when none exists. It never overwrites existing root instructions, including with `--force`; if the repository already has an `AGENTS.md`, preserve it and merge the paragraph from `examples/AGENTS.md.example` manually. `local-board doctor` reports a warning until the root instructions reference `.local-board/AGENT.md`.

```bash
git add .local-board/project.toml .local-board/AGENT.md .agents/skills AGENTS.md .gitignore
git commit -m "Configure Local Board"
```

Do not commit `.local-board/state`, `secrets`, or `backups`.

## Configure and start

Edit `.local-board/project.toml`, then validate and reconcile it:

```bash
local-board config validate
local-board config plan
local-board config apply
```

Create one identity per human and agent. Tokens are displayed once:

```bash
local-board actor owner --kind human
local-board actor coding-agent --kind agent
```

Start one server for the repository:

```bash
local-board serve
```

Open <http://127.0.0.1:8765>. Agents use `http://127.0.0.1:8765/mcp`; they never open SQLite directly.

The SQLite board is local operational state and is intentionally not transferred by Git. All worktrees on this machine should use this single server. A fresh clone receives configuration and instructions but starts with no issues, actors, or activity; Local Board currently has no multi-host database synchronization.

## Verify

With the server running and a token in the environment:

```bash
export LOCAL_BOARD_TOKEN='token-shown-once'
local-board doctor
```

Use `local-board doctor --offline` when the server is intentionally stopped. See [operations.md](operations.md) for diagnostics and data handling.
