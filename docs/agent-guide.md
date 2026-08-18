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
6. Use the latest `expected_revision` for mutations and transitions. On `conflict`, fetch fresh context before deciding whether to retry.
7. Request review and transition only through `available_transitions`. Release abandoned or handed-off work.

If MCP is unavailable, report the failure instead of creating a second task system. A human can run `local-board doctor`; an agent with shell access may run it without printing the token.

