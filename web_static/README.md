# Dashboard shell and components

`web_design.dashboard_page(path, title, body, username, csrf)` returns the
signed-in document. Pass the current route, a plain-text title, trusted HTML
without an `<h1>`, and the session's username and CSRF token. The shell supplies
the only `<h1>`, the workspace navigation, POST logout, and a focusable main
landmark. Reserve `main-content` as the skip target. Use `<h2>` to start body
sections. Do not supply a second workspace menu or logout form in the body.

`web_design.page(title, body_html)` is the separate sign-in and message shell.
For that shell, include one `<h1>` in the trusted body. Escape every untrusted
value interpolated into either body.

`GET /patterns` passes the session username and CSRF token to `patterns()`.
Calling `patterns()` without arguments renders the same reference without
account actions. It reads no inventory, runs no scripts, and makes no external
requests. Keep `style.css` beside this file when distributing the module.
The shell embeds the exact CSS returned by `web_design.styles()`;
`web_inventory.py` allows that stylesheet by hash while leaving scripts blocked.

The synthetic scope selector and fragment pager use CSS `:has()`. Without it,
the full comparison table and both linked result pages are available without
an inactive selector. Full evidence links and native provenance disclosures
work in either case. Production filters and pagination use GET.

See [DESIGN.md](../DESIGN.md) for the Primer and Carbon references.

## Shared markup

| Use | Markup |
| --- | --- |
| Sections | `.stack` spaces children vertically. `.section-heading` groups a section heading with a short explanatory paragraph. |
| Collection cards | `.panel-grid` lays out `.panel` children and collapses to one column as needed. Add `.panel--wide` to a child that should span the grid. |
| Local navigation | Use a named `<nav class="section-nav">` for subviews or in-page jumps, not primary workspace links. Use `aria-current="page"` on the current subview. |
| Filter bar | Place a labeled GET `<form class="toolbar filters">` before the results. Either wrap a field in its label or group an explicit label and field in `.filter-field`. Labels and controls share a compact desktop row; field groups can wrap on mobile. Retain query values and offer a reset link when filters are active. |
| Result summary | Put `.summary` text before the results: the visible range and total, or an explicit empty message and a way to reset. |
| Evidence table | Use a native `<table class="data-table">` with caption and scoped column and row headers. For a genuinely wide table, wrap it in `<div class="table-scroll" role="region" tabindex="0" aria-labelledby="…">` named by its caption. |
| Table preview | Use `<span class="table-preview">` for non-interactive summary text (at most two visible lines). Use the row name as a link or add a `.detail-link` outside the clamp with a record-specific accessible name. Render full values and detail-only fields in the destination, not in CSS-hidden table cells. |
| Pagination | Use a named `<nav class="pagination">` after the results. Link adjacent pages with `rel="prev"` or `rel="next"` and mark the current link with `aria-current="page"`. Carry filters in the links. |
| Provenance | Use native `<details>` with a descriptive `<summary>` and a `<dl class="facts">`. Put nested fact lists inside a `<dd>`. Use `<time datetime="…">` for observation times. |

Use `web_design.status_badge(state)` for labeled badges. Collection states are
`complete`, `partial`, `stale`, `disconnected` and `error`; evidence presence is
`observed`, `absent` or `unknown`. Map domain values explicitly (for example,
`failed` to `error`). Unsupported keys raise `KeyError`. A badge does not infer a
state: absence requires a complete scan of the right scope, and stale evidence
keeps the prior observation time. Put the reason and scope in adjacent text.

The system light and dark themes use neutral ruled surfaces, a blue action
color, clear focus outlines, and local system typography. The data track
fills the available width up to about 1,800 CSS pixels. The same icon-and-text
sidebar reflows above the data, without relying on a disclosure or hiding
links. Keep state labels visible independently of badge color.

Use native focusable elements for controls. In-page jump links can focus their
heading targets with `tabindex="-1"`.

## Verification after integration

1. Run `python3 -m unittest -v test_web_design`.
2. Open authenticated `/patterns` with JavaScript disabled. Confirm all five
   collection states, the current sidebar link, account and logout. Select
   each scope: expect 3, 2, 1, then 0 rows in option order. Verify that the
   comparison table is the only content affected.
3. Use the keyboard: the first Tab reveals the skip link; Enter moves focus
   to main. Follow a Full evidence link and verify that focus lands on the
   matching record heading. Toggle provenance with Enter or Space. Follow
   Next page, then Previous page; focus and shown range should change.
4. In both themes, at desktop and 320 CSS pixels wide, check that only the
   table region scrolls horizontally. Compare an inventory route at desktop
   width: a single left nav, filter fields in one row, and a wide results
   surface. At mobile width, all destinations and actions remain reachable.
   Check that the sign-in and message shell has no workspace navigation.
