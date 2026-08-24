# Local Board

Local Board is a lightweight issue board installed inside a repository. It gives autonomous agents and humans a shared SQLite-backed board, an MCP interface, and a local web UI without requiring a hosted service.

## Design philosophy

Local Board is a **clean board**, not a workflow engine. The server's job is synchronization — atomic claims, optimistic revisions, a stable identifier scheme, an audit trail — not enforcing how your team works. Statuses have a fixed set of categories (`backlog`, `unstarted`, `started`, `completed`, `canceled`) so tools can reason about progress, but their names are yours to configure, and transitions between them are always free: any status can move to any other status. There is no workflow graph to violate.

The rules that used to live in the server — required reviewers, branch patterns, which label blocks which transition — now live in agent instructions (`.local-board/AGENT.md`, the `local-board` skill, `CLAUDE.md`/`AGENTS.md`) where a human can read and edit them directly, instead of behind a database migration. The server enforces only what must be true for the data to stay consistent: a status has to exist, a claim has to be atomic, a dependency graph can't cycle, and (optionally) an issue can't start unassigned. Everything else — review discipline, definition of done, when "blocked" actually blocks you — is a convention your agents follow, not a constraint the server imposes.

## Is Local Board a fit?

Local Board is most useful when multiple agents, or humans and agents, work concurrently on the same local repository and need atomic claims, a shared source of truth for what's in flight, and an audit trail, without the ceremony of a hosted tracker. It can replace an ad-hoc `TODO.md` for that local coordination loop while keeping board configuration and agent instructions in Git.

It is probably unnecessary for a single short-lived agent task, and it is not a replacement for a hosted tracker used by a distributed team. Issues, comments, identities, and activity live in the ignored SQLite database: Git clones receive the board configuration and instructions, **not** the operational board. Agents in other worktrees must connect to the one server that owns the common Git repository's database. Agents on other machines need a deliberately operated shared host (with appropriate network security) or a separate board; Local Board does not synchronize databases between machines.

## Product scope

One board per repository, with:

- one prefix and one identifier sequence (`APP-12`);
- configurable statuses with fixed categories and free transitions;
- Markdown descriptions and comments — checklists are `- [ ]` / `- [x]` lines in the description, not a separate feature;
- priorities, labels, assignees, and milestones (phases, not releases);
- sub-issues via `parent_id`, and blocking dependencies with advisory `blocked` derivation;
- Git branch/commit/PR/MR references as links, not integrations;
- authenticated human and agent identities with admin/member/viewer roles;
- an immutable append-only activity log;
- MCP over Streamable HTTP at `/mcp`;
- a repository-local web board.

## Architecture

```text
Agents ── Streamable HTTP MCP ─┐
Browser ─────── HTTP API ──────┼── Local Board server ── SQLite (WAL mode)
                               └── Web UI
```

Each command opens a short SQLite transaction. WAL mode, a busy timeout, foreign keys, and uniqueness constraints provide the storage foundation for multiple local agent processes. Tokens are generated from cryptographic randomness and stored only as SHA-256 digests. The plaintext token is shown once.

The single Local Board server owns `.local-board/state/board.db` in the repository by default. The runtime directory is ignored by Git; `.local-board/project.toml` and agent instructions remain trackable. `--db` and `LOCAL_BOARD_DB` override the database location. Agents never open SQLite directly: every worktree connects to the same server URL.

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

An automated coordinator can request machine-readable bootstrap credentials with `local-board actor coordinator --kind agent --json`. After the server starts, an authenticated admin coordinator can provision a separate least-privilege identity for every subagent through the MCP `create_actor` tool and invalidate/reissue credentials with `rotate_actor_token`. Both commands return plaintext tokens once; capture them without logging and pass them through the orchestrator's secret/environment channel, never through issue comments or tracked files.

Save each displayed token securely. Start the UI and HTTP MCP endpoint:

```bash
local-board serve
```

`init` creates `.local-board/project.toml`, applies it to SQLite, and adds runtime-only paths to the target repository's `.gitignore`. Open <http://127.0.0.1:8765> and enter an actor token.

## Connect an MCP agent

Start one server for the repository and configure every agent to use its HTTP MCP endpoint:

```text
URL: http://127.0.0.1:8765/mcp
Authorization: Bearer <actor token>
```

Every agent should receive its own token so activity and authorship remain attributable. MCP clients send JSON-RPC to `POST /mcp` with `Authorization: Bearer <token>`, `Content-Type: application/json`, and `Accept: application/json, text/event-stream`. Local Board is sessionless and returns JSON responses; notifications receive HTTP 202.

`initialize` returns an `instructions` string carrying your identity, the board prefix, statuses, labels, milestones, and policy — a connecting agent needs **zero** discovery calls before it can start working. Member agents get 16 tools: `whoami`, `get_board_context`, `list_issues`, `get_issue`, `list_activity`, `create_issue`, `update_issue`, `claim_issue`, `release_issue`, `add_comment`, `update_comment`, `add_dependency`, `remove_dependency`, `add_git_link`, `create_milestone`, `create_label`. Admins additionally get label/milestone update and delete, `delete_comment`, git link update/delete, `create_actor`, `rotate_actor_token`, and `set_actor_role`. Use MCP `tools/list` for the authoritative schemas.

`local-board init` also installs `.local-board/AGENT.md`, a repository-local skill at `.agents/skills/local-board/`, and a root `AGENTS.md` discovery bridge when that file does not exist. It never overwrites existing root instructions, including with `--force`; merge [the provided bridge instructions](examples/AGENTS.md.example) manually when the repository already has an `AGENTS.md`. `local-board doctor` warns when that bridge is not discoverable. Separate onboarding is available in [the human guide](docs/human-guide.md), [the agent guide](docs/agent-guide.md), and [the operations guide](docs/operations.md).

## Human web UI

The board renders one column per configured status, in order. Cards show the identifier, title, priority, label chips, a claimed badge, and a blocked badge. Opening an issue shows its rendered Markdown description (with `- [ ]` checkboxes rendered read-only), comments, dependencies, sub-issues, and Git links; status, assignee, priority, milestone, and labels are all editable inline, subject to the same optimistic-revision rule agents use. Humans can also claim and release issues and post comments. An activity view gives repository-level oversight.

The UI uses stable issue routes such as `/api/issues/APP-12`. REST mutations honor optimistic revisions and return HTTP `409` for stale writes, with a uniform `{"error":{"code","message","retryable"}}` body. The same `Board` domain methods back REST and MCP, so blocking-dependency, claim, and revision rules stay consistent across human and agent clients. The REST API also exposes a few correction operations reserved for humans and admins (deleting a comment, retargeting a Git link) that member agents don't need day to day.

### Agent MCP workflow

1. Read `initialize`'s `instructions` once at connection time — no discovery calls needed.
2. Call `list_issues`, filtered by milestone, label, or query, then `get_issue` using an identifier such as `APP-12` for full context including the current `revision` and `blocked`.
3. Call `claim_issue` with the current `revision`, work, and use `add_comment` for decisions and handoffs.
4. Call `update_issue` with the latest `revision` to change status, priority, labels, or assignment; a stale `revision` returns a machine-readable `conflict` error — re-read before retrying, never retry blindly.
5. Call `release_issue` if abandoning work.

## Project as code

`.local-board/project.toml` is the versioned desired state for the board's identity, statuses, labels, milestones, and policy. Issues, comments, actors, tokens, activity, and Git links remain operational data in the ignored SQLite database.

```toml
schema_version = 2

[board]
prefix = "APP"
name = "Application"
description = ""

[defaults]
priority = "medium"

[agent_policy]
require_assignee_before_start = true

# Status names are yours to change; categories are the contract.
# Transitions are free: any status can move to any other status.
[[statuses]]
name = "Backlog"
category = "backlog"

[[statuses]]
name = "Todo"
category = "unstarted"

[[statuses]]
name = "In Progress"
category = "started"

[[statuses]]
name = "In Review"
category = "started"

[[statuses]]
name = "Done"
category = "completed"

[[statuses]]
name = "Canceled"
category = "canceled"

[[labels]]
key = "review_required"
name = "Review required"
color = "#f59e0b"
```

Use `local-board config validate` for syntax and semantic validation, `config plan` to inspect drift, and `config apply` to reconcile it atomically. Apply is non-destructive: entities omitted from TOML are not deleted. Config-managed statuses and labels have stable identities, and every effective apply is recorded with its digest and diff. Removing a status still used by an issue is rejected.

The configuration schema and SQLite schema are versioned independently. Commit `project.toml` to Git, but never commit `.local-board/state`, `backups`, or `secrets`.

## Statuses and blocking

Statuses have five fixed categories — `backlog`, `unstarted`, `started`, `completed`, `canceled` — but their names, order, and count within each category are entirely configurable in `project.toml`. Any status can move to any other status; there is no allowed-transitions graph to satisfy. The only server-side rule tied to status is `agent_policy.require_assignee_before_start`: when enabled, an issue can't move into a `started`-category status until it is claimed or assigned.

`blocked` is advisory, not enforced: it's derived from an issue's blocking dependencies (`add_dependency`) whose target isn't in a `completed` or `canceled` category. The server will not stop you from starting a blocked issue — that discipline lives in agent instructions (see the `local-board` skill), not in a hard check.

## Concurrent agents

Issues carry a monotonically increasing `revision`. Mutation tools accept `expected_revision`; a stale writer receives a conflict instead of silently overwriting another agent's work. Issue numbers use a transactional counter rather than `MAX(...) + 1`, so concurrent threads and server requests cannot allocate the same identifier.

Agents should call `claim_issue` before starting work. Claiming atomically assigns the authenticated actor and creates a renewable lease (30 minutes by default); only one actor can claim a given revision. Use `release_issue` to clear a claim.

SQLite still serializes writes internally. Local Board uses WAL mode and acquires
write locks with `BEGIN IMMEDIATE`. Transient lock errors use a bounded,
jittered exponential retry: by default each attempt has a 1000 ms SQLite busy
timeout, with 6 retries and a 10 ms backoff base. These values can be adjusted
through `Board(busy_timeout_ms=..., max_lock_retries=...,
retry_base_seconds=...)`. Exhaustion raises `DatabaseBusyError`; arbitrary SQL
writers that bypass `Board.transaction()` do not receive this behavior.

Optimistic revisions protect domain data from lost updates. The concurrency
suite exercises repeated simultaneous threads, synchronized independent
processes, related-record writes, and dashboard/activity readers alongside
writers, and finishes stress scenarios with SQLite integrity and foreign-key
checks.

## Git branch linking

Include an issue identifier such as `APP-12` in the branch name and run:

```bash
LOCAL_BOARD_TOKEN=... local-board sync-branch
```

This records the current branch against every matching issue. Commit and PR/MR URLs can later be associated through the `add_git_link` MCP tool without granting Local Board access to GitHub or GitLab.

## Security boundaries

The server binds to `127.0.0.1` by default. Every actor has an `admin`, `member`, or read-only `viewer` role; the first actor is the bootstrap admin and later actors default to member. Admins can change roles with `set_actor_role`, and the last admin cannot be demoted. Tokens are local bearer credentials evaluated within a single trust domain — do not expose the server to an untrusted network, and treat every token holder as able to read and write the whole board. Activity is append-only and protected by SQLite triggers as well as the domain API.

## Backup and recovery

Create a consistent online SQLite snapshot with `local-board backup [path]`. Each backup receives a JSON manifest containing its format, schema version, byte size, and SHA-256 checksum. Restore validates the checksum, SQLite integrity, and required Local Board tables before atomically replacing state:

```bash
local-board backup
local-board restore .local-board/backups/board-20260818T120000Z.db --force
```

Restore automatically writes a pre-restore safety backup when current state exists.
Stop `local-board serve` before restoring so no process retains a connection to the replaced database.

## Limitations

- **One board per repository.** There is no multi-project or multi-team partitioning within a single Local Board instance; a repository that needs more than one board needs more than one server.
- **No stdio MCP transport yet.** Only Streamable HTTP is implemented; agents that only speak stdio MCP need a bridge.
- **No data export yet.** Migrating off Local Board today means reading SQLite or the REST API directly; there is no built-in export command.
- **A single local trust domain.** Every actor token can read and write the whole board once issued; Local Board has no per-issue or per-label access control, and the server assumes it's running on a machine and network you already trust.
- **The web UI has no automated tests.** The Python surface is covered by the test suite (measured in CI); the single-file browser UI is exercised manually.

## Development

The runtime has no third-party dependencies.

```bash
python -m unittest discover -s tests/unit -v
python -m unittest discover -s tests/integration -v
python -m unittest discover -s tests/e2e -v
python -m compileall -q local_board tests
```
