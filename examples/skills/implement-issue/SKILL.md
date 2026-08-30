---
name: implement-issue
description: Implement a Local Board issue (e.g. APP-42) end-to-end following the definition-of-done loop — claim it, ground in repo truth, plan, write independent tests via a subagent, run gates, verify behaviour, review, commit with the issue reference, and close the issue out on the board. Use when asked to implement or work on a board issue.
---

Implement the Local Board issue given in the arguments (e.g. `APP-42`). Follow every
step; none is optional unless marked so.

This skill is an example: it encodes one team's definition of done on top of Local
Board's mechanics. Adapt the gates, the model tiers and the review convention to
your own project — the board enforces almost none of it.

## 0. Board access and the call budget

All board reads and writes go through Local Board MCP. Never open
`.local-board/state/board.db` directly and never commit a token.

**Write as yourself.** Use the token minted for your own actor, from
`LOCAL_BOARD_TOKEN`. Borrowing the orchestrator's admin token attributes your claims,
comments and transitions to the orchestrator — which quietly destroys the one thing
the activity journal is for.

**The board is a coordination tool, not a diary. Budget about 8 calls for a whole
issue.** Every call is a model turn, and every turn re-reads your whole context —
bookkeeping is not free, it is the most expensive part of a small issue. Four rules
keep it honest:

- **Take the briefing from the handshake.** The MCP `initialize` response carries
  your identity, the board prefix, the statuses and their categories, the labels,
  the milestones and the agent policy. That is your session context; do not spend a
  call re-fetching it. Only if your client does not surface `instructions` should
  you call `get_board_context` once.
- **Read the issue once**, at pickup, with `get_issue`. To refresh a revision later,
  call `get_issue` with `comments: "none"` — the thread is the expensive half of
  that payload and you already read it.
- **Batch every burst of small mutations into one JSON-RPC call.** The `/mcp`
  endpoint accepts an array of `tools/call` requests and answers them in one
  round-trip, so six mutations cost one model turn instead of six. `add_git_link`
  also takes `refs: [...]` and files a whole batch of links in one transaction.
- **Comment once, at delivery.** A running commentary costs a call apiece and nobody
  reads it. Progress lives in the status and in the description's checkboxes.

Track your own count. If an issue costs you more than ~12 calls, say so in the final
report with what they were spent on — that is a finding about the board, not a
personal failure.

## 1. Read the issue

- Fetch it with `get_issue`. Do not start from the title alone. This one call gives
  you the description, checkboxes, comments, labels, dependencies, the `blocked`
  flag, git links and the current `revision` — everything you need to start.
- If the acceptance criteria are ambiguous, or the issue carries a label that marks
  it as undecided (`awaiting-answer`, `decision`), ask the user first. Never guess
  scope.
- **Check the claim before anything else — the lease is the lock.** Being listed as
  `assignee` is ownership, not a claim: an assignee with no live lease does not block
  pickup, and a live lease does block it even when the assignee is someone else. Read
  `claim_expires_at`, not the name.
  - `assignee` empty, or the lease in `claim_expires_at` has passed → call
    `claim_issue` with the `revision` you just read.
  - Claimed by you → proceed.
  - Claimed by another agent with a live lease → the server will refuse your
    `claim_issue` with a `conflict` that names the holder and the expiry. Do not
    retry blindly and do not route around it with `update_issue`, which does *not*
    check the lease. Pick a different issue, or coordinate with the holder.
  - The `conflict` error is `retryable`, but it reports the expiry as an absolute
    timestamp and your clock may differ from the server's. If you must wait, wait on
    the difference between two server-reported timestamps, not on your own clock.
- **`blocked: true` means stop.** Do not start; report which dependency is open.
  `blocked` is derived from dependencies whose status category is neither
  `completed` nor `canceled` — so a *cancelled* prerequisite reads as satisfied and
  clears the flag. If the board says you are unblocked but the prerequisite was
  cancelled rather than finished, treat it as blocked and ask.
- **Claim and start in one call.** `claim_issue` takes an optional `status`, so
  `claim_issue(issue, expected_revision, status: "In Progress")` takes the lease and
  moves the issue into a `started` status in a single turn. The board must reflect
  reality for the other agents — this is not optional.
- If work stalls on a question mid-implementation, say so and label the issue
  accordingly — never leave a silently stuck started issue.

## 2. Ground in repo truth

- The repo owns truth; the issue only points to it. Read what the issue references:
  `docs/`, API contracts, the relevant `CLAUDE.md` / `AGENTS.md` files.
- **Read the contract, not the code.** If a dependency issue is done, its author's
  delivery comment should carry the public contract of what they built. Use that.
  Only fall back to reading their implementation if the comment is missing or
  contradicts the signatures — and if it is missing, say so in your report: it is
  the single most valuable thing the board carries.
- **Treat a predecessor's design notes as claims, not findings.** A comment saying a
  hazard was "sidestepped" is worth exactly as much as the run attached to it. If
  there is no run, the hazard is still open — verify it yourself before you build on
  it.
- If the issue text contradicts repo docs, the repo wins — flag the conflict instead
  of silently following the ticket.

## 3. Plan

- **Delegate codebase reconnaissance to an Explore subagent** (cheaper model): which
  files are affected, which conventions apply, what similar code already exists.
  Keep the main context for decisions, not file dumps.
- For anything non-trivial, propose the implementation plan and get approval before
  editing.
- **If the change touches a shared contract — an API schema, a wire format, a public
  interface other issues are built against — the contract change is step one of the
  plan and its own reviewable unit.** Spec diff first, then whatever is generated
  from it, then the implementation. Changing the contract last means every other
  agent read the old one while you worked.
- Record the plan as markdown checkboxes in the issue description — `- [ ]` lines,
  one per acceptance criterion, written in a single `update_issue`. There is no
  separate checklist API: the checkboxes *are* the checklist. If the issue already
  has them, use them; do not add a parallel list.

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
  implementation, treat it as signal — reconcile against the spec, never silently
  edit tests to make them pass.
- **Tell the test-writer to attack the seams, not just the happy path.** Three
  classes of input are the ones that get missed, every time: **a second writer**
  (another thread, another process, the project's own CLI against the same store),
  **a malformed request**, and **a value at the boundary of its type**. A suite that
  only walks the happy path passes while the feature loses data.
- **Write tests against the contract, not the implementation.** Capture ids from the
  constructor rather than hard-coding them; use real values at the real boundaries.
  A suite written this way survives a rewrite of the internals — which is the whole
  point of having it.

## 5. Implement

- Implement in the main context against the spec and the subagent's tests.
- Respect layer rules and repo conventions. Stay inside the issue's scope: if another
  agent owns a file, do not edit it — comment on their issue instead.
- **Fix a defect where it lives, not where it surfaced.** If a shared resource needs
  a guard, the guard belongs in the layer that owns the resource, not in each caller.
  If that layer is another agent's issue, file the fix against *that* issue and say
  so in both.
- **Stage only your own paths.** Other agents are working in this same tree;
  `git add -A` will sweep up their half-finished work — and the untracked scratch
  files the project's own README tells people to create. The board coordinates
  issues, not files — file discipline is entirely on you.

## 6. Gates (all relevant ones must pass)

- Run the repo's own checks — tests, linter, type checker, build — whatever a
  contributor runs locally. The repo's `CLAUDE.md` / `AGENTS.md` names them.
- A gate that fails is not "flaky" until proven; reproduce before dismissing.
- **A green suite is a property of the tests, not of the code.** "All tests passed,
  nothing to report" is not a finding. If you have nothing to report, say what you
  attacked and what held.

## 7. Verify behaviour

- Run the app and observe the change working. "Compiles and tests pass" is not "done"
  for user-visible work.
- **Commit the script you verified with.** An uncommitted verification run is an
  unreproducible screenshot for the next agent: cite it by path and commit, and quote
  its output in the delivery comment rather than pasting its body.
- **A design note asserting that a hazard is avoided must bring the run that shows
  it.** An untested claim of "this cannot race" is not a decision, it is a hope — and
  the next agent will inherit it as though it were verified.

## 8. Review

- Run the code-review skill on the diff. Fix confirmed findings; note skipped ones
  with reasons in the final report.
- **Commit first — review is post-commit.** Review fixes land as follow-up commits
  carrying the same issue key, not as amendments. A reviewer needs a stable commit to
  point at, and on a shared tree an amended commit pulls the ground out from under
  anyone who already fetched it.

## 9. Close out (definition of done)

Aim to spend **one comment plus one batched mutation** here.

- Any decision made during implementation lands in `docs/` or the relevant
  `CLAUDE.md` **in the same commit** — decisions must not die in the session or the
  ticket.
- Commit referencing the issue key (e.g. `APP-42: <summary>`), and put the key in the
  branch name (`feature/APP-42-login`). That naming *is* the branch-to-issue link;
  the board does not store branches.
- **One delivery comment**, and make it the artifact the next agent needs:
  - the public contract of what you built — signatures, data shapes, error mapping,
    and a small table of verified values if the logic has tricky semantics;
  - how it was verified, concretely: the committed script's path and commit, and its
    output;
  - what you deliberately did not touch;
  - anything the reviewer should look at first.
  This comment is the single highest-value thing the board holds. Write it for a
  stranger who will not read your code.
- **One `update_issue`** that does everything else at once: tick the description's
  checkboxes, set the terminal status, and adjust labels. Attach the landing
  commit(s) with `add_git_link(refs: [...])` — one call for the whole batch, or fold
  it into the same JSON-RPC array.
- **Route the issue.** Your project's own doc decides routing; this is the default.
  Move it to a review status instead of a terminal one when it carries
  `review_required` — or, as a **backstop you apply yourself**, when the work touched
  contracts, schema, auth, conventions or added a dependency. The label is set at
  triage by someone who had not seen the diff; you have. Escalate on the backstop and
  say why. The board does **not** enforce any of this — if you skip it nothing will
  stop you and the work ships unreviewed.
- **When you close without review, say so in the closing comment** — "closed without
  human review" in as many words. An unreviewed change that looks exactly like a
  reviewed one is the failure mode this line exists to prevent.
- **If the work is user-visible, add a "what to check in the app" note.** Name the
  screens and the actions, not the code. This is what an orchestrator compiles into a
  walkthrough checklist, and what a human uses when they finally look.
- **Do not call `release_issue` on work you just finished.** It clears the assignee
  as well as the lease, leaving finished work with no author. Moving to a
  `completed`- or `canceled`-category status extinguishes the lease by itself.
- **Handing off to a reviewer is the exception.** A review status is in the `started`
  category, so it does *not* extinguish your lease: leave it as-is and your reviewer
  cannot claim the issue until it expires. Release the lease when you hand off, and
  say in the delivery comment that you remain the author — the board has one
  `assignee` field doing double duty as "who holds this now" and "whose work this
  is", and the reviewer's claim will overwrite you.
- If the work shipped partially or scope changed, say so in the comment and pick the
  status that reflects reality — never claim done for unfinished work.
- Final report: what changed, how it was verified, what needs the user's manual pass,
  follow-ups worth filing, **and your board call count with a note on what was
  ceremony versus coordination.**

## 10. Reviewing someone else's issue

- **You have to find it yourself — there is no inbox.** `list_issues` filters on
  `status` and `label`, so ask for the review status and the `review_required` label
  together.
- **Expect the author's lease to still be live.** Entering a review status does not
  clear it. If `claim_issue` refuses, that is this collision, not a competing
  reviewer — wait it out or ask the author to release, and note it in your report.
- When you claim for review you become the `assignee`, silently replacing the author.
  Restore them in your closing `update_issue` unless your project says otherwise.
- Read the delivery comment first, then the diff. If the delivery comment does not
  let you scope the review, that is your first finding.
- **Actually run things.** The author's own tests passing is not evidence; they were
  written against the author's understanding. Attack the three classes their suite
  skipped: a second writer, a malformed request, a boundary value.
- **Difference the spec against the code.** For logic with rules in prose, write an
  independent model of the rules and compare it to the implementation over many
  generated inputs. It is cheap and it finds what example-based tests cannot.
- The board gives you no `approve` / `request_changes` and no way to send work back.
  Put the verdict in the first line of your comment in a fixed shape (`ACCEPT` /
  `ACCEPT WITH FOLLOW-UPS` / `REJECT — see F1..Fn`); if you reject, file the follow-up
  as a new issue rather than cancelling the original. When you finish, remove
  `review_required` and add `reviewed` in the same `update_issue` that sets the
  status — otherwise the board cannot tell "needs review" from "reviewed".
- **Review is a status, not an issue.** Do not create a separate review issue for one
  issue's review, and never make such an issue depend on the thing it reviews: that
  ticket cannot start until the work it is gating is already finished. Spin up a
  review issue only when the review is itself a deliverable across several issues —
  a release audit, a cross-cutting integration pass.

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
  adversarial review pass is Opus.
- **Escalate a stuck agent one tier up.** If an agent loops — retrying the same failing
  approach, re-reading the same files without converging, or needing repeated resumes
  on the same step — stop it and relaunch that work one tier higher with a summary of
  what was already tried. Looping burns more tokens than the bigger model would have;
  do not let a cheap model grind.
- **The board cannot help you here.** It has no notion of model class, cost or
  capability: any agent can claim any issue. Routing is entirely the orchestrator's
  job — encode it in who you hand the issue to, and say so in the issue description.
