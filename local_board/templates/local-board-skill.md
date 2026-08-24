---
name: local-board
description: Coordinate repository work through the Local Board HTTP MCP server. Use whenever an agent needs to discover, claim, implement, comment on, or complete issues on this repository's board, or hand work to another agent or human.
---

# Local Board

Local Board is a single board for this repository. Issue identifiers look like `APP-12`. If your MCP client performed `initialize`, its `instructions` field already carries your identity, the board prefix, statuses, labels, milestones, and policy — do not re-fetch them. If you connected without `initialize`, call `get_board_context` once instead. The tracked `.local-board/project.toml` is the same configuration in file form.

## Work loop

1. Call `list_issues`, filtered by the milestone, label, or query you were given (or ask the user/planner which to use).
2. Call `get_issue` to read the full description, comments, labels, dependencies, `blocked`, and current `revision`.
3. Do not start an issue whose `blocked` is `true` — finish or reassign its blockers first.
4. Call `claim_issue` with the current `revision` before you begin implementation.
5. Implement. Add short `add_comment` entries for decisions, material progress, and handoffs.
6. Call `update_issue` with the latest `revision` to move the issue to a completed-category status (e.g. `Done`) once the work is finished, or `release_issue` if you are abandoning or handing it off unfinished.

Statuses have fixed categories (backlog/unstarted/started/completed/canceled) but configurable names, and transitions between them are free — move an issue to any status at any time. There is no workflow to violate; use good judgment instead of a state machine.

## Checklists and structure

Markdown checkboxes in the description **are** the checklist — `- [ ]` and `- [x]` lines. Update them in place as you complete items; there is no separate checklist API. There are no issue types (use labels instead) and no attachments (use Markdown links to repository-relative paths). Sub-issues use `parent_id`; milestones are phases, not releases.

## Claims

Leases default to 30 minutes. Renew by calling `claim_issue` again with the latest `revision` before it expires. An expired lease lets another agent take the issue. When you move an issue to a completed- or canceled-category status the server extinguishes the lease itself — do not call `release_issue` on finished work; `release_issue` is only for abandoning or handing off unfinished work.

## Verification evidence

Put the issue identifier in your branch name (`feature/APP-12-login`) — that naming *is* the branch-to-issue link, inside git itself; the board does not store branches. When you finish, `add_git_link` the landing commit(s) and the PR/MR. Paste the verification command and its output as a fenced block in a comment, and `add_git_link` the commit that contains any verification script. The board stores task statements and outcomes, not artifacts. Any script you cite in a comment must be committed to the repository first; an uncommitted verification run is an unreproducible screenshot for the next agent. Land the artifact in git, land the outcome on the board.

## Mutation responses

Mutations return a compact confirmation — `{identifier, revision, status, category, blocked, assignee}` — not the full issue. Chain the returned `revision` into your next mutation. Pass `return_full_issue: true` only when you need the whole object, or call `get_issue`.

## Review (adapt to your project)

If the issue carries the `review_required` label, move it to "In Review" instead of a Done-category status when you finish, and comment what a reviewer should check. This is a convention, not a server rule — follow your project's actual practice if it differs. When you finish a review, remove the `review_required` label in the same `update_issue` call that changes the status (optionally add a `reviewed` label) — otherwise the board cannot distinguish "needs review" from "reviewed".

## Conflicts and errors

A conflict error means another writer changed the issue since you last read it. Re-read with `get_issue` and reconsider — never blindly retry a stale mutation.

Read the bearer token only from `LOCAL_BOARD_TOKEN`. Never expose it in issues, comments, commits, or tracked files. See [references/tools.md](references/tools.md) for the full tool list.
