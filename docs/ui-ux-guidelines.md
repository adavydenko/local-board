# Local Board UI/UX guidelines

## Purpose

Local Board should feel like a work surface rather than a configuration form: people should be able to scan work, understand its state, and make a small change without navigating through a wall of controls.

This document records the decisions behind the current web UI. Treat it as a baseline for refactors and new screens; evolve it deliberately when product capabilities change.

## Product boundaries

- The web app reflects the existing Local Board domain and API contracts. UI must not imply a persisted capability that the model does not provide.
- Project configuration is managed in `project.toml`. Settings pages explain and render that source of truth rather than creating a parallel editor.
- Blocking is the supported issue-to-issue relation. Do not add a generic **Related** control until there is a persisted, symmetric relation in the domain model.
- Checklists are out of scope for the current product and should not be reintroduced as decorative UI.

## Core principles

1. **Read first; edit on intent.** A property normally appears as concise, meaningful information. Selecting it opens an anchored picker; do not leave comboboxes visible simply because an item is editable.
2. **One clear task at a time.** Prefer small disclosures and popovers to dense, permanently expanded forms. A visible control should answer “what can I do here?” without competing with the issue itself.
3. **Keep the primary story primary.** The issue title, description, and activity belong in the main column. Supporting metadata, dependencies, structure, and Git links belong in the side rail.
4. **Show real state with familiar signals.** Status icons distinguish unstarted, active, completed, and canceled work at a glance. Group headings use the same semantics as their issues, including progress where it is meaningful.
5. **Use quiet, purposeful density.** A Linear-like rhythm means restrained surfaces, strong text hierarchy, and compact controls—not copying every Linear feature.

## Typography scale

The app uses one global type scale; new styles must pick from it rather than introduce near-adjacent sizes:

- Sizes: `10px` (uppercase micro-headings only), `11px` (metadata, timestamps, counters), `12px` (secondary UI text, buttons, hints, form labels), `13px` (base — body, rows, controls, property values), `15px` (in-content headings), `20px` (page titles), `22px` (workspace-level titles). The issue detail title is the single larger exception (`28px`).
- Weights: `400 / 500 / 600 / 700` only. Fractional or intermediate weights (550, 650) render identically to a neighbor on most system fonts and are banned.
- Monospace (identifiers, code, config preview): one family stack and one size, `12px`.
- Decorative glyphs sit outside the scale: avatar initials are sized to their circles, and icon-only symbols (`×`, `+`) use `16px`.
- Settings keeps its documented larger scale (see below); `14px` is legal only there.

## Color tokens

Every color in the stylesheet is a `var(--…)` reference to a token defined in `:root`; no hex or `rgba()` literal may appear outside that block (a unit test enforces this). Rules:

- A new color means a new token in `:root`, named for its role (`--danger-edge`, `--code-bg`), not its value.
- Status categories are colored only through `--status-backlog / -unstarted / -started / -completed / -canceled`; priority meters through `--priority-mark`. Anything status-shaped reuses these rather than inventing a nearby grey.
- Shadows are complete `--shadow-*` values (`xs / sm / menu / overlay / modal`); pick the closest existing one instead of adding a bespoke shadow.
- Text on filled dark or accent surfaces uses `--on-accent`. Label colors from board data flow through the inline `--label-color` custom property with `--label-default` as the fallback. Because that value is actor-controlled and lands inside a `style=""` attribute — where escaping stops an attribute break-out but not further CSS injection — it is emitted only by `dom.js`'s `labelColorStyle()`, which passes literal hex colors and drops anything else so the fallback applies. Never interpolate `--label-color:` into markup anywhere else (a unit test enforces this).
- This single-place palette is the prerequisite for theming: a dark theme is a `:root` override, not a stylesheet rewrite.

## Where things live

The web client is a set of native ES modules under `local_board/static/`, cut by feature so a change touches one or two files instead of a single monolith. There is no build step: `index.html` loads `js/main.js` as a module and the browser resolves the imports. Server-side, `web.py` serves `/static/<path>` with an explicit MIME table and a strict CSP.

- **A style change** → the matching `css/*.css`. Colors and scales live only in `css/tokens.css`; a change anywhere else that introduces a raw color fails the tokenization ratchet.
- **The issue list or board** → `js/views/issues.js` (and `css/issues.css`).
- **The issue detail page** — pickers, inline editing, comments, claim → `js/views/issue-detail.js` (and `css/issue-detail.css`).
- **The activity feed** → `js/views/activity.js`.
- **A settings surface** → `js/views/settings/{index,labels,milestones}.js` (and `css/settings.css`).
- **Shared plumbing**: `js/store.js` (the one mutable `store` object plus selectors over it), `js/api.js` (`fetch` wrapper, 409 handling), `js/dom.js` (`esc`, `markdown`, formatting helpers), `js/main.js` (bootstrap, routing, the shell, and every event-listener registration).

Two conventions keep the modules honest: mutable state is reached through `store.<field>` (ES module bindings are read-only from importers, so a bare exported `let` cannot be reassigned), and cross-module calls only ever run after load, so the deliberate `main.js ↔ view` import cycles are safe.

## Issue properties and pickers

- Show status, priority, assignee, milestone, and labels in a read-oriented properties rail.
- A picker opens only after the person selects a property. It is anchored to that property and labels its purpose plainly, such as “Change status.”
- Only one property or label picker may be open at once.
- Clicking elsewhere dismisses an open picker. `Escape` dismisses it and returns focus to its trigger.
- When a choice saves, return to the calm read state and restore focus to that property’s trigger. This confirms the update without leaving the person inside a stale menu.
- Do not add keyboard shortcuts until these direct interactions are stable. When shortcuts arrive, they must be discoverable, conflict-free, and never replace visible controls.

### Labels

- Render only labels attached to the issue in its properties. Never render the full label catalog as if every label were associated.
- Labels use the same colored dot and name everywhere: issue rows, issue details, creation, and settings.
- Labels are well suited to an inline picker because people often manage several at once. Keep selection in context, allow removing an applied label directly, and offer the remaining labels only after “Add label.”

### Status and progress

- Status order is meaningful. Always render it in the configured project-flow order, never as an unordered collection.
- Use the configured status category to determine the marker and behavior: clean/open for unstarted, partial ring for active, check for completed, and muted canceled marker for canceled work.
- Completed groups and issues communicate completion with the same check signal. Active group indicators may summarize the work underway; they must not claim a numeric progress model the backend cannot support.
- Creating an issue directly in a status must respect the domain’s assignment requirements. If starting work requires an assignee, guide the person to assign/claim it instead of exposing a failing state.

## Issue composition

- Avoid repeating the same navigation meaning in a top bar and breadcrumb. Keep one concise route context plus a practical back action.
- Put blocking, hierarchy, and Git links in the side rail. These are important supporting facts, not the narrative of the issue.
- Show a blocking item as a compact issue link with its status marker. Reveal destructive removal affordances on hover/focus and label them clearly for assistive technology.
- Comments need room for real discussion. Use a multiline composer, make Markdown support visible, and keep send/cancel actions adjacent to the draft.
- Correcting text is an ordinary, on-demand action. Reveal a quiet edit action on the issue narrative and on comments the person is allowed to edit; replace only the affected content with a Markdown editor, then return to read mode after save or cancel.
- Issue title and description form one narrative edit session. Comments remain independently editable by their author or an admin, and an edited marker preserves that context without requiring a full revision-history feature.

## Empty states

An empty state should state the current condition and, when the person can act, point to the next useful action. It must not feel like a dead end.

- Prefer “No blockers — this issue can move forward.” over “No blocking issues.”
- Prefer “No linked Git work yet — add a commit or pull request when it exists.” over a bare absence message.
- Prefer “No comments yet — add context, a decision, or a handoff.” where comments are actionable.
- In a status group, “No issues here yet.” is enough when the create affordance is already adjacent.
- If the user cannot act, do not promise an unavailable action; explain the configuration or permission boundary instead.
- When a search, milestone, or assignee filter is active, render only status groups that contain a matching issue. If nothing matches, show one focused empty state; retain the full workflow, including empty groups, only in the unfiltered workspace.

## Settings and configuration

- Settings should distinguish overview from editing. The overview mirrors `project.toml` and clearly marks it as managed configuration.
- Give an operational Settings area its own peer tab when it needs regular attention. Do not bury an ongoing management surface below a long configuration preview.
- Display all configured labels with their colors and all configured statuses in flow order. Never truncate a catalog in a way that makes it look complete.
- Labels have a dedicated Settings surface for the complete catalog. Configuration-managed labels remain read-only and point to `project.toml`; board-managed labels can be created or edited there.
- The issue label picker may create one new board-managed label and attach it immediately. This is an escape hatch for an active triage decision, not a second label catalog.
- Use a deliberate type scale in Settings: 22px for the page title, 15px for section headings and primary row names, 14px for explanatory copy, and 13px only for dense metadata. Do not introduce near-adjacent sizes unless they express a distinct semantic level.
- Milestone management belongs under Settings once persistence is real. Keep it a compact list: name first, then a completed-issue progress summary and an optional target date.
- A milestone list must distinguish configuration-managed entries from board-managed entries. Configuration-managed milestones are read-only in the UI and point back to `project.toml`; board-managed entries may be created or edited on demand.
- Use completed issues for milestone progress. Show both the percentage and the underlying count (for example, `50% · 3 of 6`) so the indicator remains interpretable on small milestones.
- Future prefix, tag, and status-management screens belong under Settings, but only after their persistence and permissions are real.

## Accessibility and interaction quality

- Every interactive element has a visible focus state and a specific accessible name.
- Popovers and disclosures must be usable with keyboard and pointer: focus on open, `Escape` to dismiss, and focus return on close or selection.
- Avoid hover-only meaning. Hover may reveal a secondary action, but keyboard focus must reveal and operate it too.
- Preserve layout and reading order on smaller screens; mobile can disclose the properties rail, but the issue narrative remains first.

## Review checklist for UI changes

Before merging a UI change, verify:

- Does it reflect a real product capability and existing contract?
- Does the default view prioritize reading and scanning over editing?
- Is the next action clear, especially in empty states and errors?
- Are status, labels, and milestones shown consistently with the rest of the app?
- Can a keyboard user open, use, and dismiss any new picker or disclosure?
- Does the change reduce noise or earn the additional complexity it introduces?
