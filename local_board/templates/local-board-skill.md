---
name: local-board
description: Coordinate repository work through the Local Board HTTP MCP server. Use whenever an agent needs to discover, claim, implement, comment on, or complete issues on this repository's board, or hand work to another agent or human.
---

# Local Board

Local Board is a single board for this repository. Issue identifiers look like `APP-12`. Your identity, the board prefix, statuses, labels, milestones, and policy all arrive in the MCP `initialize` response's `instructions` field — do not call discovery tools at startup, just read it once.

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

## Review (adapt to your project)

If the issue carries the `review_required` label, move it to "In Review" instead of a Done-category status when you finish, and comment what a reviewer should check. This is a convention, not a server rule — follow your project's actual practice if it differs.

## Conflicts and errors

A conflict error means another writer changed the issue since you last read it. Re-read with `get_issue` and reconsider — never blindly retry a stale mutation.

Read the bearer token only from `LOCAL_BOARD_TOKEN`. Never expose it in issues, comments, commits, or tracked files. See [references/tools.md](references/tools.md) for the full tool list.
