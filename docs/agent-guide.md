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

Subagents do not need the administrative tool surface. Their normal loop is `whoami`, project/issue discovery, `get_issue_context`, `claim_issue`, execution updates, and transitions. Local Board filters `tools/list` by the authenticated role: members do not see credential administration, and viewers see only read tools. Authorization is still enforced server-side for every call.

## Work lifecycle

1. Call `whoami`, `list_projects`, and `get_project_context`.
2. Search for existing work with `list_issues`; avoid duplicates.
3. Read `get_issue_context`, including blockers, comments, checklist, policy, transitions, and revision.
4. Create missing work with acceptance criteria and checklist, or claim existing work with `claim_issue`.
5. Keep comments, checklist, dependencies, labels, attachments, and Git links current.
6. Use the latest `expected_revision` for issue-field updates, claims, releases, and transitions. Comments, checklist items, labels, dependencies, attachments, and Git links have their own identifiers and do not change the issue revision. On `conflict`, fetch fresh context before deciding whether to retry.
7. Request review and transition only through `available_transitions`. Release abandoned or handed-off work.

Claims are leases, not permanent locks. The default lease is 30 minutes; a successful repeat `claim_issue` by the same actor renews it. For longer work, reclaim with the latest issue revision before expiry and re-read context. Stop and coordinate if the issue has been claimed by somebody else.

`available_transitions` reflects workflow edges and blocking/assignment rules, but the agent remains responsible for acceptance criteria, checklist completion, reviewer policy, and branch naming. Do not interpret an offered terminal transition as proof that the work is complete.

If MCP is unavailable, report the failure instead of creating a second task system. A human can run `local-board doctor`; an agent with shell access may run it without printing the token.
