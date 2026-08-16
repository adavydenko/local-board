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
- MCP over Streamable HTTP at `/mcp`;
- a repository-local web board.

Local Board deliberately does not reproduce Linear's entire product or API. Linear's MCP design was used as a product reference: expose focused, discoverable issue-management tools rather than leaking the database schema. The implementation uses standard MCP `initialize`, `tools/list`, and `tools/call` methods. Live verification against Linear's official documentation could not be completed in the development environment because outbound access returned HTTP 401/403; consequently this project claims MCP protocol interoperability, not tool-for-tool Linear compatibility.

## Architecture

```text
Agents ── Streamable HTTP MCP ─┐
Browser ─────── HTTP API ──────┼── Local Board server ── SQLite (WAL mode)
                               └── Web UI
```

Each command opens a short SQLite transaction. WAL mode, a busy timeout, foreign keys, and uniqueness constraints provide the storage foundation for multiple local agent processes. Tokens are generated from cryptographic randomness and stored only as SHA-256 digests. The plaintext token is shown once.

The single Local Board server owns `.local-board/state/board.db` in the repository by default. The runtime directory is ignored by Git; future project configuration and agent instructions in `.local-board/` remain trackable. `--db` and `LOCAL_BOARD_DB` override the database location. Agents never open SQLite directly: every worktree connects to the same server URL. Attachments are repository-local path references; Local Board does not copy arbitrary files into its database.

## Install in a repository

Requires Python 3.11 or newer.

```bash
python -m pip install -e /path/to/local-board
cd /path/to/your/repository
local-board init
local-board actor alice --kind human
local-board actor coding-agent --kind agent
local-board status
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

Start one server for the repository and configure every agent to use its HTTP MCP endpoint:

```text
URL: http://127.0.0.1:8765/mcp
Authorization: Bearer <actor token>
```

Every agent should receive its own token so activity and authorship remain attributable. MCP clients send JSON-RPC to `POST /mcp` with `Authorization: Bearer <token>`, `Content-Type: application/json`, and `Accept: application/json, text/event-stream`. Local Board is sessionless and returns JSON responses; notifications receive HTTP 202.

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
python -m unittest discover -s tests/unit -v
python -m unittest discover -s tests/integration -v
python -m unittest discover -s tests/e2e -v
python -m compileall -q local_board tests
```
