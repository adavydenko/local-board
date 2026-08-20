---
name: local-board
description: Coordinate repository work through the Local Board HTTP MCP server. Use whenever an agent needs to plan, discover, claim, implement, review, block, update, or complete repository issues; when creating work discovered during implementation; or when handing work to another human or agent.
---

# Local Board

Treat Local Board as the source of truth for operational work. Use MCP tools, never SQLite or Local Board HTTP internals directly.

## Start work

1. Call `whoami`. Stop and report an authentication problem if it fails.
2. Call `list_projects`, then `get_project_context` for the relevant project. Follow its workflow and `agent_policy`.
3. Call `list_issues` with a focused query before creating work. Avoid duplicates.
4. Call `get_issue_context` before acting. Read the description, checklist, comments, dependencies, `blocked`, available transitions, assignee, reviewer, and `revision`.
5. If no issue exists, call `create_issue` with a concise title, Markdown acceptance criteria, correct type and priority, then add a checklist.
6. Call `claim_issue` with the current revision before implementation. On `conflict`, fetch fresh context; never blindly retry a stale mutation.

## Describe new work

- Choose `task` for bounded implementation or documentation with a known outcome; `bug` for behavior that contradicts an expected result; `feature` for a new or materially expanded user-visible capability; `chore` for maintenance, tooling, dependencies, or cleanup without a direct behavior change; and `epic` only for a multi-issue outcome that will be decomposed and linked.
- Choose priority from impact and urgency: `urgent` for active security, data-loss, or production-stop incidents; `high` for major impact or a near deadline without a reasonable workaround; `medium` for ordinary planned work; `low` for minor deferrable improvements; and `none` only when the project intentionally leaves prioritization unset. Do not inflate priority to gain attention.
- Write the description in Markdown with the problem or context, scope, acceptance criteria, constraints or risks, and validation plan. Add a checklist of concrete, independently verifiable completion items and keep it current; do not mark an item complete before evidence exists.

## Execute and coordinate

- Use the stable identifier such as `APP-12` in tool calls and the branch name.
- Claims are 30-minute leases by default. For longer work, renew by calling `claim_issue` again with the latest revision before expiry; stop if ownership changed.
- Call `transition_issue` only with a value from `available_transitions`; pass the latest `expected_revision`.
- Mark checklist items as work completes. Add short comments for decisions, material progress, blockers, and handoffs.
- Model prerequisites with `add_dependency`. Do not start an issue whose context says `blocked`.
- Do not take an issue assigned to another actor. Comment and coordinate instead.
- Create a new issue for material newly discovered work; do not silently expand scope.
- Attach repository-relative artifacts and Git references with `add_attachment` and `add_git_link`.
- Follow `agent_policy.branch_pattern` rather than inventing a global branch convention, and always include the stable issue identifier. After creating or switching to that branch, run `local-board sync-branch` with the token supplied only through `LOCAL_BOARD_TOKEN`. Use `add_git_link` for later commit and PR/MR links.

## Finish or hand off

1. Fetch fresh issue context.
2. Confirm acceptance criteria and checklist completion.
3. Link the branch, commit, PR, or MR as available.
4. Add a concise outcome or handoff comment.
5. Transition to review when required and ensure a reviewer is assigned.
6. Transition to the configured terminal state only when policy permits. Call `release_issue` when abandoning or handing off unfinished work.

`available_transitions` does not by itself prove completion. The agent must still enforce acceptance criteria, checklist completion, reviewer requirements, and the configured branch pattern.

## Error handling

- `conflict`: another writer changed the issue. Fetch context and reconsider the operation.
- `blocked`: resolve the dependency or assignment/policy condition; do not bypass it.
- `not_found`: rediscover projects, actors, labels, milestones, or the issue identifier.
- `invalid_request`: inspect `tools/list` and correct the arguments.

Read the bearer token only from `LOCAL_BOARD_TOKEN`. Never expose it in issues, comments, activity, logs, command arguments, commits, or tracked files. Read [references/tools.md](references/tools.md) when selecting less common tools.
