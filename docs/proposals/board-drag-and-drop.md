# Proposal: drag-and-drop on the board view

Status: proposed — awaiting a decision; no implementation yet.

## Problem

The board view now matches the list on affordances (per-column create, rich cards), but a
column layout visually promises one interaction it does not deliver: dragging a card to
another column. The UX audit called this out — without it the board "promises more
interactivity than it provides". Keyboard and pointer users can already change status
through the anchored status picker, so drag-and-drop is an acceleration, not the only path.

## Scope

Dragging a card between columns changes the issue's **status** — nothing else.

- The server side is already sufficient: `PATCH /api/issues/{identifier}` with `status`
  honors optimistic revisions; a `409` means someone changed the issue mid-drag, and the
  client reloads the dashboard and drops the move.
- **No ordering within a column.** The domain model has no per-status ordering, so a drop
  position inside a column cannot persist. Cards keep their current sort. If ordering ever
  becomes a product need, that is a schema change to discuss separately, not a UI detail.
- The existing status picker remains the accessible, keyboard-first path; drag-and-drop
  adds no `aria` obligations beyond marking the drag handles.

## Options

### 1. Native HTML5 drag-and-drop

`draggable="true"` plus `dragstart/dragover/drop`. Zero code beyond handlers, but the API
ignores touch devices entirely, the drag preview is a browser-generated ghost that cannot
be styled reliably, and scroll-during-drag near the container edge must be reimplemented
anyway. Fine for a desktop-only tool; a poor fit for the mobile layout this UI already
supports.

### 2. Pointer Events by hand — **recommended**

`pointerdown` with a small movement threshold (so plain clicks still open the issue),
`setPointerCapture`, a cloned card following the pointer, column highlight under the
pointer, `PATCH` on release, optimistic column move with rollback on error. Works
identically for mouse and touch, the preview is our own DOM, and edge auto-scroll of
`.board-layout` is a few lines. Estimated 150–250 lines of JS plus ~30 of CSS, no
dependencies — consistent with the package's zero-dependency stance. The Playwright suite
can drive it with `page.mouse` for regression coverage.

### 3. A component framework plus a DnD library

Adopting Preact (vendored, ~5.5 KB gzip, no build step) and rebuilding the board as
components, with drag state held in component state. This is the right call **only** as
part of the broader component-model decision in
[split-index-html.md](split-index-html.md) — drag-and-drop alone does not justify it, but
it has been the strongest single trigger for that migration in every discussion so far.

## Recommendation

Implement **option 2** after the index.html split (so the code lands in `js/views/board.js`
rather than growing the monolith), unless the split proposal's Preact stage is approved
first — in that case fold DnD into the board component (option 3) and skip the interim
vanilla implementation.

## Acceptance criteria

- Dragging a card to another column issues one `PATCH` and the card lands in the target
  column without a full-page flash; on `409` or network failure the card returns and a
  toast explains why.
- Click without movement still opens the issue; touch drag works on the mobile layout.
- The status picker continues to work unchanged.
- A Playwright test drives a drag with `page.mouse` and asserts the status change and the
  rollback path.
