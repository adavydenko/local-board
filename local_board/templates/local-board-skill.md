---
name: local-board
description: Coordinate repository work through the Local Board HTTP MCP server. Use whenever an agent needs to plan work into milestones and issues, discover, claim, implement, review, comment on, or complete issues on this repository's agile board, or hand off work to another agent or human.
---

# Local Board

Local Board is this repository's agile board — one project per repository. Issues are identified as `{prefix}-{number}`, e.g. `APP-12`.

## Session context

Your MCP client performs the MCP protocol handshake (`initialize`) automatically when it connects. The response's `instructions` field already carries your identity, the project prefix, statuses, labels, milestones, and policy — do not re-fetch them. This handshake is unrelated to `local-board init`, the operator's one-time repository bootstrap. If your client does not surface `instructions`, call `get_board_context` once; call `whoami` when you only need to re-check who you are. The tracked `.local-board/project.toml` is the same configuration in file form.

## Work loop

1. Call `list_issues`, filtered by the milestone, label, or query you were given (or ask the user/planner which to use). When you are the planner, work the other direction: turn the plan into milestones and issues with acceptance criteria before implementation agents start.
2. Call `get_issue` to read the full description, comments, labels, dependencies, blockers, and revision.
3. Do not start an issue whose `blocked` is `true` — finish or reassign its blockers first. The server does not enforce this: `blocked` is a signal derived from open dependencies, not a lock, and honoring it is this instruction's job.
4. Call `claim_issue` with the current `revision` before you start working on it — implementation, planning, testing, and review alike.
5. Work. Add short `add_comment` entries for decisions, material progress, and handoffs.
6. When the work is finished, call `update_issue` with the latest `revision` to move the issue to a completed-category status (e.g. `Done`). When abandoning or handing off unfinished work, first `add_comment` why — what blocked you, what you learned, what remains — and set any label or status that helps the next agent, then call `release_issue`.

Statuses have fixed categories (backlog/unstarted/started/completed/canceled) but configurable names, and transitions between them are free — the server allows any move, even Backlog straight to Done. Free transitions are what makes mistakes correctable; the expected path for real work is the loop above.

## Checklists and structure

Markdown checkboxes in the description **are** the checklist — `- [ ]` and `- [x]` lines. Update them in place as you complete items; there is no separate checklist API. There are no issue types (use labels instead) and no attachments (use Markdown links to repository-relative paths). Sub-issues use `parent_id`; milestones are phases, not releases.

## Claims

Claiming makes you the assignee and grants a lease (default 30 minutes) — the lease is the mutual-exclusion window, the assignee is the attribution. Renew by calling `claim_issue` again with the latest `revision` before it expires; an expired lease lets another agent take the issue. Moving an issue to a completed- or canceled-category status extinguishes the lease itself but keeps you as assignee — do not call `release_issue` on finished work. `release_issue` is only for abandoning or handing off unfinished work; it clears both the lease and the assignee.

## Verification evidence

Put the issue identifier in your branch name (`feature/APP-12-login`) — that naming *is* the branch-to-issue link, inside git itself; the board does not store branches. When you finish, `add_git_link` the landing commit(s) and the PR/MR. A comment may quote the verification command and its output as a fenced block, but never paste a script's body into a comment: commit the script to the repository and reference its path and commit instead. An uncommitted verification run is an unreproducible screenshot for the next agent. Land the artifact in git, land the outcome on the board.

## Mutation responses

Mutations return a compact confirmation — `{identifier, revision, status, category, blocked, assignee}` — not the full issue. Chain the returned `revision` into your next mutation. Pass `return_full_issue: true` only when you need the whole object, or call `get_issue`.

## Review (adapt to your project)

If the issue carries the `review_required` label, move it to "In Review" instead of a Done-category status when you finish, and comment what a reviewer should check. Reviewers pick up review work by claiming the issue — the board does not auto-assign reviewers; if your project routes reviews to a dedicated agent or human, that routing lives in your project's instructions, not in the server. This whole section is a convention, not a server rule — follow your project's actual practice if it differs. When you finish a review, remove the `review_required` label in the same `update_issue` call that changes the status (optionally add a `reviewed` label) — otherwise the board cannot distinguish "needs review" from "reviewed".

## Conflicts and errors

A conflict error means another writer changed the issue since you last read it. The error names the current revision — re-read with `get_issue` and reconsider; never blindly retry a stale mutation.

## Authentication

Read the bearer token only from the `LOCAL_BOARD_TOKEN` environment variable. Tokens are minted by an admin (`create_actor` / `rotate_actor_token`) — normally the operator or orchestrator that launched you — and delivered through the process environment or a secret store, never through tracked files. If the variable is missing, stop and ask your operator instead of guessing, and never expose a token in issues, comments, commits, or tracked files. See [references/tools.md](references/tools.md) for the full tool list.
