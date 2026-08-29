# Proposal: split index.html into feature modules

Status: proposed — awaiting a decision; no implementation yet.

## Problem, measured

The whole web client lives in one `local_board/static/index.html` (~1,300 dense lines:
CSS, markup, and JS in three sections). Measured on this branch's history:

- **Every UI commit touches this one file.** A typical feature ("Add label management
  settings") lands as 10–16 hunks scattered across all three sections; two logically
  independent features collide in the same regions, so parallel work guarantees merge
  conflicts.
- **Editing anything requires reading everything.** The file is ~33k tokens — about 44%
  of the token weight of the entire codebase — which is exactly the "large context"
  cost anyone (human or agent) pays for a one-button change.
- Long minified lines (p99 ≈ 384 chars) make line-based diffs and merges coarse.

## Target structure — cut by feature, not by technology

Splitting into `index.html + app.css + app.js` would not help: independent features would
still collide inside `app.js`. The cut is vertical:

```
static/
  index.html              shell only (~60 lines)
  css/
    tokens.css            the :root palette and scales — the only file with raw values
    base.css              reset, typography, shared controls
    shell.css             sidebar, header, navigation
    issues.css            list + board
    issue-detail.css      detail page, rail, comments
    settings.css          settings surfaces
  js/
    main.js               bootstrap, routing, login
    api.js                fetch wrapper, errors, 409 handling
    state.js              data/identity/current issue + selectors
    dom.js                esc, markdown, relativeTime, safeExternalUrl
    icons.js              the SVG set
    views/
      issues.js           list + board rendering and handlers
      issue-detail.js
      activity.js
      settings/{index,labels,milestones}.js
```

After this, "add label management" is `settings.css` + `views/settings/labels.js` — zero
overlap with a milestone feature. A short "where things live" map in
`ui-ux-guidelines.md` lets an agent load two small files instead of the whole app.

## What it costs

- **`web.py`:** ~20 lines — a static route with an extension whitelist (`.css`, `.js`,
  `.svg`), path-traversal guard, `Content-Type` and `Cache-Control`. Today `web.py` serves
  exactly one file.
- **Packaging:** `package-data` glob widens from `static/*` to `static/**/*`.
- **No build step.** `<script type="module">` resolves imports natively in every supported
  browser; `dependencies = []` is untouched; round-trips are free on localhost.
- Existing markup-string tests in `tests/unit/test_web_ui.py` that assert against
  `index.html`'s text need their file targets updated (the Playwright suite is unaffected
  and is the real safety net for the move).

## What we get beyond mergeability

- **A real Content-Security-Policy becomes possible.** With all CSS/JS inline,
  `default-src 'self'` is unusable; after the split the server can send a strict CSP —
  meaningful hardening for a server that holds bearer tokens inside other people's repos.
- `tokens.css` makes the palette/scale ratchets trivially reviewable, and a dark theme
  becomes a small override file.

## Order of operations

1. Split CSS into the `css/` files and slim `index.html` to the shell (pure moves; the
   Playwright suite guards behavior).
2. Split JS into ES modules under `js/` (pure moves, no reformatting of history).
3. **Optional next stage — Preact per view:** vendor `preact + hooks + htm` (three pinned
   files, ~5.5 KB gzip total, still no build step) and convert one view at a time,
   starting with the smallest (`settings/labels`). This is the component-model decision:
   it removes the hand-rolled escape discipline (~110 manual `esc()` calls), the
   69-line click dispatcher, and the duplicated mobile/desktop properties rail. It is
   **not** required for stages 1–2 and can be decided after seeing them land.

## Explicitly not proposed

A bundler (Vite/esbuild/Bun), a runtime npm dependency, React, or committing build
artifacts. If stage 3 is approved, its cost is three vendored files in `static/vendor/`,
version-pinned and hash-verifiable against npm.

## Interaction with drag-and-drop

[board-drag-and-drop.md](board-drag-and-drop.md) recommends implementing DnD after this
split so the code lands in `js/views/issues.js` (or a board component, if stage 3 is
approved first) instead of growing the monolith.
