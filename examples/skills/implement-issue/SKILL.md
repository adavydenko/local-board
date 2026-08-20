---
name: implement-issue
description: Implement a Local Board issue (e.g. APP-42) end-to-end following the definition-of-done loop — claim it, ground in repo truth, plan, write independent tests via a subagent, run gates, verify behaviour, review, commit with the issue reference, and close the issue out on the board. Use when asked to implement or work on a board issue.
---

Implement the Local Board issue given in the arguments (e.g. `APP-42`). Follow every step;
none is optional unless marked so.

## 0. Board access and the call budget

All board reads and writes go through Local Board MCP. Never open
`.local-board/state/board.db` directly and never commit a token.

**The board is a coordination tool, not a diary. It has a call budget: about
12 calls for a whole issue.** Every call is a model turn, and every turn re-reads
your whole context — bookkeeping is not free, it is the most expensive part of a
small issue. Three rules keep it honest:

- **Read the full context exactly once**, at pickup (`get_issue_context`). It is a
  30 KB payload and half of it is the activity log. Never call it a second time to
  "refresh the revision" — use `get_available_transitions`, which returns the
  revision, status, blocked flag and legal transitions in under 400 bytes.
- **Batch every burst of small mutations into one JSON-RPC call.** The endpoint
  accepts an array of `tools/call` requests and answers them in one round-trip, so a
  six-item checklist is one turn, not six. Batch: checklist creation, checklist
  completion, labels, git links.
- **Comment once, at delivery.** A running commentary costs a full issue echo per
  entry and nobody reads it. Progress lives in the status and the checklist.

Track your own count. If an issue costs you more than ~15 calls, say so in the final
report with what they were spent on — that is a finding about the board, not a
personal failure.

## 1. Read the issue

- Fetch it with `get_issue_context`. Do not start from the title alone. This one call
  gives you the description, acceptance criteria, checklist, comments, dependencies,
  blocked flag, git links and legal transitions — everything you need to start.
- If the acceptance criteria are ambiguous, or the issue carries a label that marks it
  as undecided (`awaiting-answer`, `decision`), ask the user first. Never guess scope.
- **Check the claim before anything else — the claim is the lock.**
  - `assignee` empty, or the lease in `claim_expires_at` has passed → call
    `claim_issue` with the `revision` you just read. If it returns a `conflict`,
    another agent won the race: pick a different issue, do not retry blindly.
  - Claimed by you → proceed.
  - Claimed by another agent with a live lease → STOP and ask before taking it.
    `update_issue` would let you steal it silently; that is not a licence to.
  - A `reviewer` set to someone else is not a claim — it means they review your work.
- **`blocked: true` means stop.** Do not start; report which dependency is open.
  Note that a *cancelled* dependency currently reads as satisfied — if the board says
  you are unblocked but the prerequisite was cancelled rather than finished, treat it
  as blocked and ask.
- **Once claimed and scope is confirmed, transition to `in_progress`.** The board must
  reflect reality for the other agents — this is not optional. If the workflow has a
  `backlog → todo` step still pending, do both transitions in one batch.
- If work stalls on a question mid-implementation, say so and label the issue
  accordingly — never leave a silently stuck `in_progress` issue.

## 2. Ground in repo truth

- The repo owns truth; the issue only points to it. Read what the issue references:
  `docs/`, API contracts, the relevant `CLAUDE.md` / `AGENTS.md` files.
- **Read the contract, not the code.** If a dependency issue is `done`, its author's
  delivery comment should carry the public contract of what they built. Use that.
  Only fall back to reading their implementation if the comment is missing or
  contradicts the signatures — and if it is missing, say so in your report: it is the
  single most valuable thing the board carries.
- If the issue text contradicts repo docs, the repo wins — flag the conflict instead
  of silently following the ticket.

## 3. Plan

- **Delegate codebase reconnaissance to an Explore subagent** (cheaper model): which
  files are affected, which conventions apply, what similar code already exists. Keep
  the main context for decisions, not file dumps.
- For anything non-trivial, propose the implementation plan and get approval before
  editing.
- Record the plan as the issue's checklist — **created in a single batched call**, one
  item per acceptance criterion. If the issue already has a checklist, use it; do not
  add a parallel one.

## 4. Tests — independent, via subagent

- **Skip this entire step for issues with no meaningful logic** — styling, copy,
  config tweaks, doc edits. Say so in the final report ("test-writer skipped: no
  testable logic"). Existing gates still run.
- For logic with testable acceptance criteria, **spawn a test-writer subagent** whose
  prompt contains ONLY: the issue's acceptance criteria, the relevant contract
  excerpt, and the target module's public interface. **It must never see the
  implementation** (which may not exist yet — that is fine and preferred, TDD-style).
- Rationale: independence. Tests written by the context that wrote the logic inherit
  its misreadings; an isolated test-writer cannot. When its tests disagree with the
  implementation, treat it as signal — reconcile against the spec, never silently edit
  tests to make them pass.
- **Tell the test-writer to attack the seams, not just the happy path**: concurrency
  and interleaving, partial failure, malformed input, and the boundaries named in the
  acceptance criteria. A suite that only walks the happy path passes while the feature
  loses data.

## 5. Implement

- Implement in the main context against the spec and the subagent's tests.
- Respect layer rules and repo conventions. Stay inside the issue's scope: if another
  agent owns a file, do not edit it — comment on their issue instead.
- **Stage only your own paths.** Other agents are working in this same tree; `git add -A`
  will sweep up their half-finished work. The board coordinates issues, not files —
  file discipline is entirely on you.

## 6. Gates (all relevant ones must pass)

- Run the repo's own checks — tests, linter, type checker, build — whatever a
  contributor runs locally. The repo's `CLAUDE.md` / `AGENTS.md` names them.
- A gate that fails is not "flaky" until proven; reproduce before dismissing.

## 7. Verify behaviour

- Run the app and observe the change working. "Compiles and tests pass" is not "done"
  for user-visible work.

## 8. Review

- Run the code-review skill on the diff. Fix confirmed findings; note skipped ones with
  reasons in the final report.

## 9. Close out (definition of done)

Aim to spend **one batched call plus one comment plus one transition** here.

- Any decision made during implementation lands in `docs/` or the relevant `CLAUDE.md`
  **in the same commit** — decisions must not die in the session or the ticket.
- Commit referencing the issue key (e.g. `APP-42: <summary>`).
- **One batched call**: close the checklist items, attach the git links
  (`add_git_link` with the commit sha and the branch).
- **One delivery comment**, and make it the artifact the next agent needs:
  - the public contract of what you built — signatures, data shapes, error mapping,
    and a small table of verified values if the logic has tricky semantics;
  - how it was verified, concretely (what you ran, what it produced);
  - what you deliberately did not touch;
  - anything the reviewer should look at first.
  This comment is the single highest-value thing the board holds. Write it for a
  stranger who will not read your code.
- **Route the issue**: if the issue type requires a reviewer, or the work touched
  contracts, schema, auth or conventions, transition to `in_review` and make sure a
  reviewer is set — the board does **not** enforce this, so if you skip it nothing
  will stop you and the work ships unreviewed. Otherwise transition to the terminal
  state.
- **Do not call `release_issue` on an issue you just closed.** It clears the assignee
  as well as the lease, leaving a finished issue with no author. Note that a terminal
  transition does not clear the lease either — the issue will keep showing as claimed
  until the lease expires. That is a board defect, not something to fix with more calls.
- If the work shipped partially or scope changed, say so in the comment and pick the
  state that reflects reality — never claim done for unfinished work.
- Final report: what changed, how it was verified, what needs the user's manual pass,
  follow-ups worth filing, **and your board call count with a note on what was
  ceremony versus coordination.**

## 10. Reviewing someone else's issue

If you are the `reviewer` on an issue sitting in `in_review`:

- You have to find it yourself — there is no inbox. `list_issues` and filter for
  `status: in_review` with your own actor id (from `whoami`) in `reviewer_id`.
- Read the delivery comment first, then the diff. If the delivery comment does not
  let you scope the review, that is your first finding.
- **Actually run things.** The author's own tests passing is not evidence; they were
  written against the author's understanding. Attack the seams the author's suite
  ignored — concurrency, interleaving, malformed input, resource exhaustion.
- The board gives you no `approve` / `request_changes` and no way to send work back:
  from `in_review` the only exits are the terminal state and `cancelled`. So put the
  verdict in the first line of your comment in a fixed shape (`ACCEPT` /
  `ACCEPT WITH FOLLOW-UPS` / `REJECT — see F1..Fn`) and, if you reject, file the
  follow-up as a new issue rather than cancelling the original.

## 11. Models to use

Distinguish layers of intelligence:

- **Top tier — Fable.** The orchestrator itself, milestone review, and the rare truly
  cross-cutting / high-blast-radius calls: wire contracts, authority semantics, core
  state machines. Roughly: the things the board marks urgent.
- **High tier — Opus.** Architecture within one subsystem, integration seams, tradeoff
  analysis — and hard debugging (a root-cause hunt across layers is Opus-shaped work
  even when the eventual fix is small).
- **Mid tier — Sonnet.** Well-specced implementation in logical isolation: the outcome
  is verifiable by itself and could not lead to serious issues — a screen from a mock,
  a rule module with a TDD suite, unit tests.
- **Chore tier — Haiku.** Mechanical work with no judgment in it: bulk renames, log
  scraping, fixture generation.

Rules:

- **When spawning subagents, ALWAYS specify the model explicitly.** An unspecified
  spawn silently inherits the caller's model — usually the orchestrator's — which is
  the exact waste this section exists to stop. Pass the alias (`sonnet` / `opus` /
  `haiku`), never a pinned model id: aliases track the latest release.
- **Inner subagents get tiers too**: the independent test-writer is Sonnet; an
  adversarial review pass may be Opus.
- **Escalate a stuck agent one tier up.** If an agent loops — retrying the same failing
  approach, re-reading the same files without converging — stop it and relaunch that
  work one tier higher with a summary of what was already tried. Looping burns more
  tokens than the bigger model would have.
- **The board cannot help you here.** It has no notion of model class, cost or
  capability: any agent can claim any issue, and `list_issues` cannot even filter by
  label. Routing is entirely the orchestrator's job — encode it in who you hand the
  issue to, and say so in the issue description.
