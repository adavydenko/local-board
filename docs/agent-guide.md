# Agent guide

## Connect

Use the repository-provided `.agents/skills/local-board/SKILL.md`. Connect the MCP client to:

```text
Transport: Streamable HTTP
URL: http://127.0.0.1:8765/mcp
Authorization: Bearer ${LOCAL_BOARD_TOKEN}
```

Each agent must receive its own token through the process environment or another untracked secret store. Never put tokens in MCP examples committed to Git.

The conceptual client configuration in `examples/mcp-http.example.json` illustrates the required URL and header; adapt field names to the MCP client in use.

## Coordinator bootstrap

An agent orchestrator with shell access can initialize the repository and capture its first admin identity without parsing human-oriented output:

```bash
local-board init
local-board actor coordinator --kind agent --json
local-board serve
```

The JSON response contains the one-time plaintext token. Capture it directly into the orchestrator's secret store; do not echo it, place it in a prompt, add it to an issue, or commit it. Once connected as admin, call `create_actor` for each subagent with the minimum role (`member` for implementation, `viewer` for audit). Deliver each returned token through the execution environment or the orchestrator's secret channel. Call `rotate_actor_token` when a token may have leaked or when reassigning an identity; the previous token stops authenticating immediately.

Subagents do not need the administrative tool surface. Local Board filters `tools/list` by the
authenticated role: members do not see credential administration or correction tools, and viewers see
only read tools. Authorization is still enforced server-side for every call.

## Work lifecycle

1. `initialize` already returns your identity and the board snapshot (prefix, statuses with
   categories, labels, milestones, policy) in its `instructions` — no discovery calls are needed.
2. Find work with `list_issues`, filtered by the milestone, label, or issue list your planner or user
   gave you; read the full issue with `get_issue` before acting.
3. Claim existing work with `claim_issue`, or create missing work with `create_issue` — put the
   acceptance criteria in the Markdown description; `- [ ]` checkboxes are the checklist.
4. Update fields, labels, and status with a single `update_issue` call. Transitions are free: pick
   the status by its category, and treat `blocked: true` as a signal to finish blockers first.
5. Record decisions, progress, and handoffs with `add_comment`; link branches and PRs with
   `add_git_link`.
6. Pass the latest `expected_revision` on every mutation. On `conflict`, re-read the issue before
   deciding whether to retry.
7. Move the issue to a `completed`-category status when done, or `release_issue` when abandoning
   or handing off unfinished work.

Claims are leases, not permanent locks. The default lease is 30 minutes; a successful repeat
`claim_issue` by the same actor renews it. For longer work, reclaim with the latest issue revision
before expiry and re-read context. Stop and coordinate if the issue has been claimed by somebody else.

The server enforces only invariants: statuses must exist, revisions must be current, claims are
atomic, dependency cycles are rejected, and policy may require an assignee before a `started`
status. Everything else — review rules, branch naming, when work counts as complete — is the
agent's and the user's responsibility, expressed in repository instructions, not in the server.

If MCP is unavailable, report the failure instead of creating a second task system. A human can run `local-board doctor`; an agent with shell access may run it without printing the token.
