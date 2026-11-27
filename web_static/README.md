# Inventory web patterns

`web_design.page(title, body_html)` returns the complete HTML document. Pass a
plain-text title and trusted HTML for the main content. Include one `<h1>` in
that content and escape every value interpolated into it. The shell supplies the
site header, main landmark, skip link and stylesheet. Reserve `main-content` as
the shell's skip target.

Return `web_design.patterns()` from `GET /patterns` when wiring the HTTP layer.
The pattern page uses the same shell and components as application pages; it
reads no inventory and makes no external requests. Keep `style.css` beside this
file when distributing the module. `web_design.styles()` returns that CSS; the
shell embeds it, so a `/style.css` route is not required. If the HTTP layer sets
a Content Security Policy, allow the exact inline stylesheet by hash rather than
enabling arbitrary inline styles.

## Shared markup

| Use | Markup |
| --- | --- |
| Report introduction | `<header class="report-header stack">` with an optional `.eyebrow`, the page heading and a `.lede` paragraph. |
| Report sections | `.stack` spaces children vertically. `.section-heading` groups a heading with a short explanatory paragraph. |
| Collection cards | `.panel-grid` lays out `.panel` children and collapses to one column as needed. Add `.panel--wide` to a child that should span the grid. |
| Evidence table | `<table class="data-table">` with a caption and scoped column and row headers. Keep the table narrow enough to wrap at mobile widths. |
| Provenance | `<details class="disclosure">` with a descriptive `<summary>`, followed by `<dl class="facts">` with grouped terms and descriptions. Use `<time datetime="…">` for observation times. |

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
2. Open `/patterns` through the application. Confirm all five collection states
   and the evidence table render without any external network request.
3. Use only the keyboard: first Tab reveals the skip link; Enter moves focus to
   main; the next Tab reaches the first in-page link. Follow each in-page link
   and verify focus reaches its heading. Toggle provenance with Enter or Space,
   then reverse through the links with Shift+Tab. Check that focus stays visible.
4. Inspect in both system themes at desktop and 320 CSS pixels wide, including
   at browser zoom. Confirm the cards reflow, the table remains readable, and
   the page does not scroll horizontally. Check text and badge contrast (at
   least 4.5:1) and focus outlines against adjacent surfaces (at least 3:1).
