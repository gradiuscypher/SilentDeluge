# Inventory web patterns

`web_design.page(title, body_html)` returns the complete HTML document. Pass a
plain-text title and trusted HTML for the main content. Include one `<h1>` in
that content and escape every value interpolated into it. The shell supplies the
site header, main landmark, skip link and stylesheet. Reserve `main-content` as
the shell's skip target.
The workspace renderer owns its navigation, including the link to `/patterns`.
The shell intentionally does not add a second menu or a second patterns link.

`GET /patterns` renders `web_design.patterns()` with the same shell and
components as the workspace. It reads no inventory, runs no scripts, and makes
no external requests. Keep `style.css` beside this file when distributing the
module. The shell embeds the exact CSS returned by `web_design.styles()`;
`web_inventory.py` allows that stylesheet by hash while leaving scripts blocked.

The synthetic scope selector and fragment pager use CSS `:has()`. With no
support for that selector (or with styles blocked), the page shows the full
table and both linked result pages without an inactive filter. Provenance uses
native `<details>` in either case. Workspace filters and pagination use GET.

## Shared markup

| Use | Markup |
| --- | --- |
| Report introduction | `<header class="report-header stack">` with an optional `.eyebrow`, the page heading and a `.lede` paragraph. |
| Report sections | `.stack` spaces children vertically. `.section-heading` groups a heading with a short explanatory paragraph. |
| Collection cards | `.panel-grid` lays out `.panel` children and collapses to one column as needed. Add `.panel--wide` to a child that should span the grid. |
| Navigation | Named `<nav class="workspace-nav">` with links, optionally in a list. Use `aria-current="page"` on the actual current route. |
| Filter bar | A labeled GET `<form class="filters">` in a `.toolbar`, or a form with both classes. Wrap each label and field in one group. Retain query values on submit and offer a reset link when filters are active. |
| Result summary | Put `.summary` text before the results: the visible range and total, or an explicit empty message and a way to reset. |
| Evidence table | Use a native `<table>` with caption and scoped column and row headers. For a genuinely wide table, wrap it in `<div class="table-scroll" role="region" tabindex="0" aria-labelledby="…">` named by its caption. |
| Pagination | Use a named `<nav class="pagination">` after the results. Link adjacent pages with `rel="prev"` or `rel="next"` and mark the current link with `aria-current="page"`. Carry filters in the links. |
| Provenance | Use native `<details>` with a descriptive `<summary>` and a `<dl class="facts">`. Put nested fact lists inside a `<dd>`. Use `<time datetime="…">` for observation times. |

Use `web_design.status_badge(state)` for labeled badges. Collection states are
`complete`, `partial`, `stale`, `disconnected` and `error`; evidence presence is
`observed`, `absent` or `unknown`. Map domain values explicitly (for example,
`failed` to `error`). Unsupported keys raise `KeyError`. A badge does not infer a
state: absence requires a complete scan of the right scope, and stale evidence
keeps the prior observation time. Put the reason and scope in adjacent text.

The GRDS field-guide palette in `style.css` uses the OS light/dark preference:
neutral canvas and ruled surfaces, blue for actions, and magenta for detail and
keyboard focus. Headings and labels use a local monospace stack; body copy uses
system sans. Keep the reading track bounded and let navigation and table cells
wrap instead of hiding labels or columns. Badge foregrounds and backgrounds are
paired per theme; keep state labels visible independently of color.

Use native focusable elements for controls. In-page jump links can focus their
heading targets with `tabindex="-1"`.

## Verification after integration

1. Run `python3 -m unittest -v test_web_design`.
2. Open `/patterns` through the application with JavaScript disabled. Confirm
   all five collection states render and no external requests are made. Select
   each scope and return to All scopes: expect 3, 2, 1, then 0 rows in option order.
3. Use only the keyboard: the first Tab reveals the skip link; Enter moves focus
   to main. Follow the section jumps and verify that focus reaches their headings.
   Toggle provenance with Enter or Space. Follow Next page, then Previous page:
   focus should move to the displayed result page, and the summary should change.
4. In both system themes at desktop and 320 CSS pixels wide, including at
   browser zoom, check that cards, navigation, filters, and pager reflow. Focus
   the table region and scroll to its last column without horizontal page scroll.
   Check text and badge contrast (at least 4.5:1) and focus outlines against
   adjacent surfaces (at least 3:1).
