"""Local HTML shell and inventory presentation; no request or data access."""

from html import escape
from pathlib import Path


_CSS = Path(__file__).with_name("web_static").joinpath("style.css").read_text(encoding="utf-8")
_STATUS_LABELS = {
    "complete": "Complete",
    "partial": "Partial",
    "stale": "Stale",
    "disconnected": "Disconnected",
    "error": "Error",
    "observed": "Observed",
    "absent": "Absent",
    "unknown": "Unknown",
}


def styles() -> str:
    """Return the local CSS shared by all pages."""
    return _CSS


def page(title: str, body_html: str) -> str:
    """Wrap trusted HTML in the site shell. The title is plain text.

    Callers supply the main heading and escape any untrusted values in body_html.
    """
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>{styles()}</style>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <header class="site-bar">
    <div class="shell">
      <span class="wordmark">SilentDeluge</span>
    </div>
  </header>
  <main class="shell" id="main-content" tabindex="-1">
{body_html}
  </main>
</body>
</html>'''


def status_badge(state: str) -> str:
    """Render a labeled inventory badge. An unsupported state raises KeyError."""
    label = _STATUS_LABELS[state]
    return f'<span class="status-badge" data-state="{state}">{label}</span>'


def patterns() -> str:
    """Render the local design reference using synthetic evidence only."""
    return page("Inventory patterns", f'''
    <div class="patterns stack">
      <header class="report-header stack">
        <p class="eyebrow">Design reference / Synthetic examples</p>
        <h1>Inventory patterns</h1>
        <p class="lede">Show what was observed, where it came from, and what the evidence cannot tell us.</p>
        <p><a href="/">Back to inventory</a></p>
        <nav class="workspace-nav" aria-label="On this page">
          <ul>
            <li><a href="#collection-states">Collection states</a></li>
            <li><a href="#inventory-evidence">Inventory evidence</a></li>
            <li><a href="#evidence-detail">Evidence detail</a></li>
            <li><a href="#paged-results">Paged results</a></li>
          </ul>
        </nav>
      </header>

      <section class="stack" aria-labelledby="collection-states">
        <header class="section-heading">
          <h2 id="collection-states" tabindex="-1">Collection states</h2>
          <p>Report coverage and freshness separately from the state of an instance.</p>
        </header>
        <div class="panel-grid">
          <article class="panel panel--wide">
            <p>{status_badge("complete")}</p>
            <h3>All instances checked</h3>
            <p>Only a successful, complete scan can establish absence, and only in the scope it checked.</p>
          </article>
          <article class="panel">
            <p>{status_badge("partial")}</p>
            <h3>Coverage is limited</h3>
            <p>A metrics capture can show instances it found. A name missing from it remains unknown.</p>
          </article>
          <article class="panel">
            <p>{status_badge("stale")}</p>
            <h3>Last verified state retained</h3>
            <p>The instance was running at 10:00 UTC. That observation was 10 minutes old at the 10:10 UTC attempt.</p>
          </article>
          <article class="panel">
            <p>{status_badge("disconnected")}</p>
            <h3>No source configured</h3>
            <p>No approved source is configured for this scope. There is no evidence to report.</p>
          </article>
          <article class="panel">
            <p>{status_badge("error")}</p>
            <h3>Collection could not finish</h3>
            <p>The source did not respond. This attempt produced no evidence and does not replace prior observations.</p>
          </article>
        </div>
      </section>

      <section class="stack" aria-labelledby="inventory-evidence">
        <header class="section-heading">
          <h2 id="inventory-evidence" tabindex="-1">Inventory evidence</h2>
          <p>Pair presence with its basis. Keep the observation timestamp attached to the evidence.</p>
        </header>
        <div class="panel pattern-inventory">
          <div class="toolbar filters pattern-scope-filter" hidden>
            <div>
              <label for="pattern-scope">Scope</label>
              <select id="pattern-scope">
                <option value="all">All scopes</option>
                <option value="main">Main stage</option>
                <option value="rehearsal">Rehearsal stage</option>
                <option value="storage">Storage stage</option>
              </select>
            </div>
          </div>
          <div class="summary" role="status">
            <p data-example-summary="all">Showing all 3 example instances.</p>
            <p data-example-summary="main" hidden>Showing 2 of 3 example instances for Main stage.</p>
            <p data-example-summary="rehearsal" hidden>Showing 1 of 3 example instances for Rehearsal stage.</p>
            <p data-example-summary="storage" hidden>No matching example instances. Choose All scopes to reset the filter.</p>
          </div>
          <div class="table-scroll" role="region" aria-labelledby="example-inventory-caption" tabindex="0">
            <table>
              <caption id="example-inventory-caption">Example inventory evidence</caption>
              <thead>
                <tr>
                  <th scope="col">Instance</th>
                  <th scope="col">Presence</th>
                  <th scope="col">Scope and basis</th>
                  <th scope="col">Evidence time</th>
                </tr>
              </thead>
              <tbody>
                <tr data-example-scope="main">
                  <th scope="row">paper-moon</th>
                  <td>{status_badge("observed")}</td>
                  <td>Listed in a complete scan of Main stage.</td>
                  <td><time datetime="2025-01-01T10:00:00Z">10:00 UTC</time></td>
                </tr>
                <tr data-example-scope="main">
                  <th scope="row">tin-piano</th>
                  <td>{status_badge("absent")}</td>
                  <td>Missing from a complete scan of Main stage.</td>
                  <td><time datetime="2025-01-01T10:00:00Z">10:00 UTC</time></td>
                </tr>
                <tr data-example-scope="rehearsal">
                  <th scope="row">painted-sun</th>
                  <td>{status_badge("unknown")}</td>
                  <td>Missing from a partial capture of Rehearsal stage.</td>
                  <td><time datetime="2025-01-01T10:05:00Z">10:05 UTC</time></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="stack" aria-labelledby="evidence-detail">
        <header class="section-heading">
          <h2 id="evidence-detail" tabindex="-1">Evidence detail</h2>
          <p>Show the finding first. Expand the supporting facts without leaving the page.</p>
        </header>
        <div class="panel">
          <h3>paper-moon</h3>
          <p>{status_badge("observed")} in Main stage</p>
          <details>
            <summary>Observation provenance for paper-moon</summary>
            <dl class="facts">
              <div><dt>Observed at</dt><dd><time datetime="2025-01-01T10:00:00Z">1 Jan 2025, 10:00 UTC</time></dd></div>
              <div><dt>Coverage</dt><dd>Complete for Main stage</dd></div>
              <div>
                <dt>Source</dt>
                <dd>
                  Synthetic instance scan
                  <dl class="facts">
                    <div><dt>Method</dt><dd>Instance listing</dd></div>
                    <div><dt>Scope</dt><dd>Main stage</dd></div>
                  </dl>
                </dd>
              </div>
            </dl>
          </details>
        </div>
      </section>

      <section class="stack pattern-pages" aria-labelledby="paged-results">
        <header class="section-heading">
          <h2 id="paged-results" tabindex="-1">Paged results</h2>
          <p>State the visible range of results and use links for the adjacent pages.</p>
        </header>
        <article class="panel" id="pattern-page-1" tabindex="-1" aria-labelledby="example-runs-1">
          <h3 id="example-runs-1">Example runs · page 1</h3>
          <p class="summary">Showing 1–2 of 4 example runs.</p>
          <ul>
            <li>Main stage scan · <time datetime="2025-01-01T10:00:00Z">10:00 UTC</time></li>
            <li>Rehearsal stage capture · <time datetime="2025-01-01T10:05:00Z">10:05 UTC</time></li>
          </ul>
          <nav class="pagination" aria-label="Example run pages">
            <ul>
              <li><a href="#pattern-page-1" aria-label="Page 1" aria-current="page">1</a></li>
              <li><a href="#pattern-page-2" aria-label="Page 2">2</a></li>
              <li><a href="#pattern-page-2" rel="next">Next page</a></li>
            </ul>
          </nav>
        </article>
        <article class="panel" id="pattern-page-2" tabindex="-1" aria-labelledby="example-runs-2">
          <h3 id="example-runs-2">Example runs · page 2</h3>
          <p class="summary">Showing 3–4 of 4 example runs.</p>
          <ul>
            <li>Main stage scan · <time datetime="2025-01-01T10:10:00Z">10:10 UTC</time></li>
            <li>Rehearsal stage capture · <time datetime="2025-01-01T10:15:00Z">10:15 UTC</time></li>
          </ul>
          <nav class="pagination" aria-label="Example run pages">
            <ul>
              <li><a href="#pattern-page-1" rel="prev">Previous page</a></li>
              <li><a href="#pattern-page-1" aria-label="Page 1">1</a></li>
              <li><a href="#pattern-page-2" aria-label="Page 2" aria-current="page">2</a></li>
            </ul>
          </nav>
        </article>
      </section>
    </div>''')
