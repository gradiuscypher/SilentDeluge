# Inventory interface design reference

SilentDeluge is a working inventory, not a landing page. Give the results
the widest track, with clear headings, lightweight rules, and visible state and
provenance. `/patterns` uses the production shell and local components with
synthetic evidence. The product fetches no design-library code, fonts or assets.

## Compact workspace

- **One workspace navigation.** [Carbon UI shell left panel](https://www.carbondesignsystem.com/building-blocks/core/components/ui-shell-left-panel/guidelines)
  provides a model for frequently switched sections. Put the brand, icon and text
  links, signed-in account and logout together in the left sidebar; do not
  repeat them in a desktop masthead. Mark the active link with a left rule and
  stronger text as well as color. `aria-current="page"` marks an exact match;
  `aria-current="location"` marks the parent of a nested route. Reflow the same
  links into a short grid on narrow screens; no separate menu or hidden destinations.
  The sign-in and message shell has a narrow reading track and no workspace menu.
- **Use width for data.** The primary track expands to about 1,800 CSS pixels.
  [GitHub Primer DataTable](https://primer.style/product/components/data-table/)
  pairs a descriptive title with a named, scannable table and row headers for
  item identity. [Carbon Data table](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)
  gives dense tables room in the main area, with a toolbar before the rows
  and pagination after them. Use the references as patterns, not dependencies.
- **Compare rows before opening details.** The [W3C WAI tables tutorial](https://www.w3.org/WAI/tutorials/tables/)
  calls for captions and explicit header associations. Inventory tables keep a
  caption, `scope="col"` on column headers and `scope="row"` on record names;
  show state, scope and time in compact cells. Render only the list fields and
  intentionally short previews in the list, not the full payload. A preview
  can wrap to two visual lines; link to the complete record by name or with a
  row-specific label, on a focusable link outside the clamp. Do not replace a
  table with cards, remove mobile columns, or put detail-only fields in hidden markup.
- **Keep filters and counts together.** Following Carbon's toolbar placement and
  [USWDS search guidance](https://designsystem.digital.gov/components/search/#guidance),
  put a labeled GET form above the results. Keep labels beside their fields
  in one desktop row; wrap field pairs on small screens. Retain active values
  and use a real submit button and reset link. Place a `.summary` before the
  results with the visible range and total, or a clear empty state and reset.
  An empty filtered result is not evidence that an asset is absent.
- **Page only when needed.** [GOV.UK pagination](https://design-system.service.gov.uk/components/pagination/)
  places the pager after the results, omits it for a single page, and drops
  Previous or Next at the limits. Use links inside a named `.pagination` nav,
  mark the current page, and carry active filters across page links. State the
  visible range and total near the results. Do not use infinite scroll.
- **Disclose secondary facts, not the finding.** [GOV.UK summary lists](https://design-system.service.gov.uk/components/summary-list/)
  use a description list for facts such as timestamps and metadata. Keep the
  finding and its evidence status visible; use native `details` and a descriptive
  `summary` for provenance. Nest a `.facts` list inside a `dd` for related fields,
  keeping terms, values and indentation in reading order. Following [WCAG Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html),
  badge text carries state even without color; adjacent text explains the basis
  and distinguishes unknown, absent and stale.
- **Keep focus and layout intact.** [WCAG Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)
  permits local scrolling for tables that need a two-dimensional layout, not
  for their filters, title or pagination. Wrap ordinary tables; give genuinely
  wide ones a named, keyboard-focusable `.table-scroll` container. Let controls
  and text wrap at 320 CSS pixels. Keep a visible, persistent outline on
  controls and focusable jump targets per [WCAG Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html).
  Compact navigation still provides at least 24 × 24 CSS pixel link targets,
  following [WCAG Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).

`/patterns` reads no inventory. Its scope selector filters only the synthetic
comparison rows and its example pager uses local fragment links. Full evidence
targets work without CSS. Production filtering and paging use ordinary GET
forms and query links; detail-only fields are rendered in the detail view.
