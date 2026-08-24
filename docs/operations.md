# Operations guide

## Diagnostics

`local-board doctor` validates:

- TOML syntax and semantics;
- configuration drift;
- supported SQLite schema;
- SQLite integrity and foreign keys;
- Bearer authentication;
- MCP `initialize` and `tools/list`.

```bash
local-board doctor --offline
LOCAL_BOARD_TOKEN=... local-board doctor --url http://127.0.0.1:8765/mcp
local-board doctor --offline --json
```

A `WARN` does not fail the command; for example, unapplied config drift is a warning. Any `FAIL` returns a non-zero exit status.

## Runtime layout

```text
AGENTS.md                    tracked discovery bridge (created only when absent)
.local-board/project.toml   tracked desired state
.local-board/AGENT.md       tracked repository policy
.local-board/state/         ignored SQLite runtime
.local-board/backups/       ignored future backup location
.local-board/secrets/       ignored local secrets
```

Run one `local-board serve` process for the common Git repository. All local worktrees and agents should connect to that server URL rather than starting parallel servers or accessing the database. The database is not synchronized through Git or between hosts.

## Keeping the server alive

`local-board serve` is a plain foreground process with no built-in supervisor. If agents depend on it for hours, run it under your own supervisor — a systemd `--user` unit, foreman, or a simple restart loop. `.local-board/state/server.json` holds the live server's URL and PID and is removed on clean shutdown; if it exists but its PID is dead, the server died uncleanly (`local-board doctor` reports this). Fatal crashes leave a traceback in `.local-board/state/server-crash.log`.

## Recovery boundary

Use `local-board backup [path]` to create a consistent online snapshot and checksum manifest. Do not treat a plain filesystem copy of `board.db` during active WAL writes as a valid backup.

Stop `local-board serve` before restoring, then run:

```bash
local-board restore .local-board/backups/board-YYYYMMDDTHHMMSSZ.db --force
```

Restore validates the manifest checksum, SQLite integrity, and required tables, and writes a pre-restore safety backup when current state exists. Backups can transfer state deliberately, but Local Board does not merge divergent databases or provide live multi-host synchronization.
