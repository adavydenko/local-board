# Human guide

## Install into an existing repository

Local Board requires Python 3.11 or newer. Install the tool in an isolated environment; while developing locally, point pipx at this checkout:

```bash
pipx install /path/to/local-board
cd /path/to/product-repository
local-board init
```

`init` creates and applies `.local-board/project.toml`, installs repository-local agent instructions and the Local Board skill, adds runtime paths to `.gitignore`, and creates `.local-board/state/board.db`. Review and commit only the tracked configuration and instruction files:

```bash
git add .local-board/project.toml .local-board/AGENT.md .agents/skills .gitignore
git commit -m "Configure Local Board"
```

Do not commit `.local-board/state`, `secrets`, `attachments`, or `backups`.

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

## Verify

With the server running and a token in the environment:

```bash
export LOCAL_BOARD_TOKEN='token-shown-once'
local-board doctor
```

Use `local-board doctor --offline` when the server is intentionally stopped. See [operations.md](operations.md) for diagnostics and data handling.

