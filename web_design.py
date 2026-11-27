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
    return _page(title, body_html)


def _page(title: str, body_html: str, *, patterns_current: bool = False) -> str:
    current = ' aria-current="page"' if patterns_current else ""
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
      <nav aria-label="Site">
        <a href="/patterns"{current}>Design patterns</a>
      </nav>
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
    return _page("Inventory patterns", f'''
    <div class="patterns stack">
      <header class="report-header stack">
        <p class="eyebrow">Design reference / Synthetic examples</p>
        <h1>Inventory patterns</h1>
        <p class="lede">Show what was observed, where it came from, and what the evidence cannot tell us.</p>
        <nav class="section-nav" aria-label="On this page">
          <a href="#collection-states">Collection states</a>
          <a href="#inventory-evidence">Inventory evidence</a>
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
        <div class="panel">
          <table class="data-table">
            <caption>Example inventory across two scopes</caption>
            <thead>
              <tr><th scope="col">Instance</th><th scope="col">Presence</th><th scope="col">Basis</th></tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">paper-moon</th>
                <td>{status_badge("observed")}</td>
                <td>
                  Listed in a complete scan of the main stage at
                  <time datetime="2025-01-01T10:00:00Z">10:00 UTC</time>.
                </td>
              </tr>
              <tr>
                <th scope="row">tin-piano</th>
                <td>{status_badge("absent")}</td>
                <td>
                  Missing from a complete scan of the main stage at
                  <time datetime="2025-01-01T10:00:00Z">10:00 UTC</time>.
                </td>
              </tr>
              <tr>
                <th scope="row">painted-sun</th>
                <td>{status_badge("unknown")}</td>
                <td>
                  Missing from a partial capture of the rehearsal stage at
                  <time datetime="2025-01-01T10:05:00Z">10:05 UTC</time>.
                </td>
              </tr>
            </tbody>
          </table>
          <details class="disclosure">
            <summary>Observation provenance for paper-moon</summary>
            <dl class="facts">
              <div><dt>Source</dt><dd>Synthetic instance scan</dd></div>
              <div><dt>Scope</dt><dd>Main stage</dd></div>
              <div><dt>Observed at</dt><dd><time datetime="2025-01-01T10:00:00Z">1 Jan 2025, 10:00 UTC</time></dd></div>
              <div><dt>Coverage</dt><dd>Complete for the main stage</dd></div>
            </dl>
          </details>
        </div>
      </section>
    </div>''', patterns_current=True)
