# Local Board

Local Board is a lightweight issue tracker installed inside a repository. It gives autonomous agents and humans a shared SQLite-backed board, an MCP interface, and a local web UI without requiring a hosted service.

## Product scope

The first release includes:

- projects and milestones;
- `task`, `bug`, `feature`, `chore`, and `epic` issues;
- a separately configurable workflow for every issue type;
- Markdown descriptions and comments;
- priorities, ordering, assignees, and reviewers;
- checklists, labels, attachment references, and issue dependencies;
- branch/commit/PR/MR references and issue-key matching for local branches;
- authenticated human and agent identities;
- an editable activity journal;
- MCP over stdio and Streamable HTTP-compatible JSON-RPC at `/mcp`;
- a repository-local web board.

Local Board deliberately does not reproduce Linear's entire product or API. Linear's MCP design was used as a product reference: expose focused, discoverable issue-management tools rather than leaking the database schema. The implementation uses standard MCP `initialize`, `tools/list`, and `tools/call` methods. Live verification against Linear's official documentation could not be completed in the development environment because outbound access returned HTTP 401/403; consequently this project claims MCP protocol interoperability, not tool-for-tool Linear compatibility.

## Architecture

```text
MCP clients ── stdio ─┐
                      ├── domain service ── SQLite (WAL mode)
Browser ─ HTTP API ───┤
MCP clients ─ /mcp ───┘
```

Each command opens a short SQLite transaction. WAL mode, a busy timeout, foreign keys, and uniqueness constraints make multiple local agent processes safe while keeping deployment to one ignored database file. Tokens are generated from cryptographic randomness and stored only as SHA-256 digests. The plaintext token is shown once.

The database is stored at `.local-board/board.db` by default and the whole directory is ignored by Git. Attachments are repository-local path references; Local Board does not copy arbitrary files into its database.

## Install in a repository

Requires Python 3.11 or newer.

```bash
python -m pip install -e /path/to/local-board
cd /path/to/your/repository
local-board init
local-board actor alice --kind human
local-board actor coding-agent --kind agent
```

Save each displayed token securely. Start the UI and HTTP MCP endpoint:

```bash
local-board serve
```

Open <http://127.0.0.1:8765> and enter an actor token. Create the first project through MCP, or with an HTTP call:

```bash
curl -X POST http://127.0.0.1:8765/api/projects \
  -H "Authorization: Bearer $LOCAL_BOARD_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"key":"APP","name":"Application"}'
```

## Connect an MCP agent

For a stdio MCP client, add a server entry equivalent to:

```json
{
  "mcpServers": {
    "local-board": {
      "command": "local-board",
      "args": ["mcp"],
      "env": {
        "LOCAL_BOARD_TOKEN": "<actor token>",
        "LOCAL_BOARD_DB": "/absolute/repository/path/.local-board/board.db"
      }
    }
  }
}
```

Every agent should receive its own token so activity and authorship remain attributable. HTTP MCP clients can send JSON-RPC requests to `POST /mcp` with `Authorization: Bearer <token>`.

Available tools cover project and issue discovery, creation and update, workflow transitions, milestones, comments, checklists, labels, dependencies, attachment references, Git links, and activity. Use MCP `tools/list` for the authoritative schemas.

## Workflows

New projects get the following states for each issue type:

```text
backlog → todo → in_progress → in_review → done
   └────────────── any active state ──────────→ cancelled
```

Agents may replace states and allowed directed transitions with `set_workflow`. Invalid or skipped transitions are rejected transactionally.

## Git branch linking

Include an issue identifier such as `APP-12` in the branch name and run:

```bash
LOCAL_BOARD_TOKEN=... local-board sync-branch
```

This records the current branch against every matching issue. PR/MR URLs can later be associated through the `add_git_link` MCP tool without granting Local Board access to GitHub or GitLab.

## Security boundaries

The server binds to `127.0.0.1` by default. Bearer tokens provide identity, not fine-grained authorization: every valid actor currently has full board access. Do not expose the server to an untrusted network. Markdown is rendered as plain text in the MVP UI, avoiding script injection. Activity entries are stored as ordinary rows and can be corrected or deleted through explicit MCP tools because the requested journal is editable.

## Development

The runtime has no third-party dependencies.

```bash
python -m unittest discover -s tests -v
python -m compileall -q local_board tests
```
