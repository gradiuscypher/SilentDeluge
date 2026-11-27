# Inventory interface design reference

Use the GRDS Pages field-guide pattern for evidence, not a marketing dashboard. The operator-side reference was reviewed from the design library's `grds-pages.md` and `grds-practice.md` specimens. The running product must not fetch that library, external fonts or private examples. `/patterns` is the local source of working examples; this note explains the design decisions.

- A ruled masthead and short section labels orient the reader. Keep the primary reading track bounded; move secondary navigation above it on narrow screens.
- Preserve a visible provenance strip beside the observations: source, scope, observation time and coverage. Status must have a text label, not just a color. Failed collection is different from a successful empty result.
- Use the reference's neutral surface and ink contrast, blue action and restrained contrasting detail, thin rules, monospace labels and visible keyboard focus. Don't reuse brand text or copy a landing-page hero into a dense inventory view.
- Exercise the actual shared styles at phone and desktop widths. Synthetic samples in the pattern library should include complete, partial, stale, disconnected and error states; never use a real capture as a design fixture.

## Compact workspace

- **Orient without duplicating links.** The shared shell provides the masthead and
  skip link; the workspace owns one named `.workspace-nav`, including the link
  to `/patterns`. The reference has its own in-page jumps and a way back to the
  overview. Following [GOV.UK service navigation](https://design-system.service.gov.uk/components/service-navigation/),
  mark the current workspace page with `aria-current="page"` and a visible
  change beyond color. Let the list wrap rather than hide destinations.
- **Compare rows before opening details.** The [W3C WAI tables tutorial](https://www.w3.org/WAI/tutorials/tables/)
  calls for captions and explicit header associations. Inventory tables keep a
  caption, `scope="col"` on column headers and `scope="row"` on record names;
  show state, scope and time in compact cells. Do not replace a table with a
  set of unrelated cards, hide columns on narrow screens, or dump raw JSON.
- **Keep filters and counts together.** The [USWDS search guidance](https://designsystem.digital.gov/components/search/#guidance)
  recommends labeled inputs, keeping search terms in the result view, and a
  native submit button. Workspace filters use a `.toolbar` and a GET `.filters`
  form; label each field, keep active values, and give search room where space
  allows. Place a `.summary` before the results: say what is shown, the total
  matching count when known, or that there are no matches and how to reset.
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

`/patterns` uses only fixed synthetic fixtures. The scope selector and pager
show local states via CSS, with no scripts, private resources, or request-time
inventory. Workspace filtering and paging use ordinary query links and forms.
