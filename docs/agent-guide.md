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
