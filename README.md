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
local-board config validate
local-board config plan
local-board actor alice --kind human
local-board actor coding-agent --kind agent
local-board status
local-board doctor --offline
```

Save each displayed token securely. Start the UI and HTTP MCP endpoint:

```bash
local-board serve
```

`init` creates `.local-board/project.toml`, applies it to SQLite, and adds runtime-only paths to the target repository's `.gitignore`. Open <http://127.0.0.1:8765> and enter an actor token. Projects may also be created imperatively through MCP or HTTP when needed:

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

`local-board init` also installs `.local-board/AGENT.md` and a repository-local skill at `.agents/skills/local-board/`. Separate onboarding is available in [the human guide](docs/human-guide.md), [the agent guide](docs/agent-guide.md), and [the operations guide](docs/operations.md).

## Human web UI

The local UI renders columns from the selected project's configured workflows rather than a fixed status list. Humans can filter issue types, open complete issue context, claim or release work, assign assignees and reviewers, change priority, complete checklist items, comment, and perform only policy-aware transitions. Project and activity views provide repository-level oversight.

The UI uses stable issue routes such as `/api/issues/APP-12`. REST mutations honor optimistic revisions and return HTTP `409` for stale writes. The same `Board` domain methods back REST and MCP, so workflow, blocking dependency, assignment, and revision rules remain consistent across human and agent clients.

### Agent MCP workflow

The MCP contract is self-discovering and uses stable references instead of requiring agents to know SQLite IDs:

1. Call `whoami`, then `list_projects` and `get_project_context` to discover identities, workflows, labels, milestones, defaults, and agent policy.
2. Search with `list_issues`; fetch the complete Markdown description, discussion, checklist, blockers, attachments, Git links, activity, and allowed transitions with `get_issue_context` using an identifier such as `APP-12`.
3. Call `claim_issue` with the current `revision`, update checklist items and comments while working, and attach dependencies or Git references as needed.
4. Call `transition_issue` with the latest `revision`; stale mutations return a machine-readable `conflict` error and should be retried only after reading fresh context.
5. Call `release_issue` if abandoning work.

Tool input schemas include enums, defaults, constraints, and stable-reference descriptions. Tool errors expose `conflict`, `not_found`, or `invalid_request` codes in `structuredContent.error` instead of requiring agents to parse prose.

## Project as code

`.local-board/project.toml` is the versioned desired state for project metadata, labels, defaults, and per-type workflows. Issues, comments, actors, tokens, activity, and Git links remain operational data in the ignored SQLite database.

```toml
schema_version = 1

[project]
key = "APP"
name = "Application"
description = ""

[defaults]
issue_type = "task"
priority = "medium"

[agent_policy]
require_assignee_before_start = true
require_reviewer_for = ["feature", "bug"]
branch_pattern = "{issue_key}-{slug}"

[[labels]]
key = "backend"
name = "Backend"
color = "#64748b"

[workflows.task]
initial = "backlog"
terminal = ["done", "cancelled"]
states = ["backlog", "todo", "in_progress", "in_review", "done", "cancelled"]
transitions = [
  ["backlog", "todo"],
  ["todo", "in_progress"],
  ["in_progress", "in_review"],
  ["in_review", "in_progress"],
  ["in_review", "done"],
]
```

Use `local-board config validate` for syntax and semantic validation, `config plan` to inspect drift, and `config apply` to reconcile it atomically. Apply is non-destructive: entities omitted from TOML are not deleted. Config-managed workflows and labels have stable keys and every effective apply is recorded with its digest and diff. Removing a workflow state that is used by an issue is rejected.

The configuration schema and SQLite schema are versioned independently. Commit `project.toml` to Git, but never commit `.local-board/state`, `backups`, `attachments`, or `secrets`.

## Workflows

New projects get the following states for each issue type:

```text
backlog → todo → in_progress → in_review → done
   └────────────── any active state ──────────→ cancelled
```

Manual projects may replace states and allowed directed transitions with `set_workflow`. Config-managed workflows must be changed in `project.toml` and reapplied; direct mutation is rejected. Invalid or skipped transitions are rejected transactionally.

## Concurrent agents

Issues carry a monotonically increasing `revision`. Mutation and transition tools accept `expected_revision`; a stale writer receives a conflict instead of silently overwriting another agent's work. Issue identifiers and board positions use transactional counters rather than `MAX(...) + 1`, so concurrent threads and server requests cannot allocate the same value.

Agents should call `claim_issue` before starting work. Claiming atomically assigns the authenticated actor and creates a renewable lease (30 minutes by default); only one actor can claim a given revision. Use `release_issue` to clear a claim. Projects whose `agent_policy.require_assignee_before_start` is enabled reject transitions into `in_progress` until an issue is claimed or assigned.

SQLite still serializes writes internally. Local Board uses WAL mode and a busy timeout, while optimistic revisions protect domain data from lost updates. The concurrency suite exercises simultaneous threads and independent processes and finishes with SQLite integrity and foreign-key checks.

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
