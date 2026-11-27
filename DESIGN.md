# Inventory interface design reference

Use the GRDS Pages field-guide pattern for evidence, not a marketing dashboard. The operator-side reference was reviewed from the design library's `grds-pages.md` and `grds-practice.md` specimens. The running product must not fetch that library, external fonts or private examples. `/patterns` is the local source of working examples; this note explains the design decisions.

- A ruled masthead and short section labels orient the reader. Keep the primary reading track bounded; move secondary navigation above it on narrow screens.
- Preserve a visible provenance strip beside the observations: source, scope, observation time and coverage. Status must have a text label, not just a color. Failed collection is different from a successful empty result.
- Use the reference's neutral surface and ink contrast, blue action and restrained contrasting detail, thin rules, monospace labels and visible keyboard focus. Don't reuse brand text or copy a landing-page hero into a dense inventory view.
- Exercise the actual shared styles at phone and desktop widths. Synthetic samples in the pattern library should include complete, partial, stale, disconnected and error states; never use a real capture as a design fixture.
