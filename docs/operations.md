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
.local-board/project.toml   tracked desired state
.local-board/AGENT.md       tracked repository policy
.local-board/state/         ignored SQLite runtime
.local-board/backups/       ignored future backup location
.local-board/attachments/   ignored local artifacts
.local-board/secrets/       ignored local secrets
```

Run one `local-board serve` process per repository checkout. All local worktrees and agents should connect to that server URL rather than starting parallel servers or accessing the database.

## Recovery boundary

Backup/restore and release upgrades are planned for the operational-hardening phase. Until those commands exist, stop the server before handling the SQLite files and preserve `board.db`, `board.db-wal`, and `board.db-shm` together. Do not treat a plain copy of `board.db` during active WAL writes as a valid backup.

