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

_SECTIONS = (
    ("/", "Overview", "M3 3h5v5H3z M12 3h5v5h-5z M3 12h5v5H3z M12 12h5v5h-5z"),
    ("/machines", "Machines", "M2 4h16v10H2z M7 17h6 M10 14v3"),
    ("/incus", "Incus", "m10 2 7 4v8l-7 4-7-4V6z M3 6l7 4 7-4 M10 10v8"),
    ("/unifi", "UniFi", "M2 7a12 12 0 0 1 16 0 M5 10a8 8 0 0 1 10 0 M8 13a3 3 0 0 1 4 0 M10 16v.1"),
    ("/relationships", "Relationships", "M3 3h4v4H3z M13 3h4v4h-4z M8 13h4v4H8z M5 7v3h10V7 M10 10v3"),
    ("/collectors", "Collectors", "M3 3h14v14H3z M3 7h14 M7 11h6 M7 14h3"),
    ("/access", "Access", "M10 2 3 5v5c0 4 3 6 7 8 4-2 7-4 7-8V5z m-3 8 2 2 4-4"),
    ("/patterns", "Design patterns", "M3 3h14v14H3z M3 7h14 M8 7v10"),
)
_BRAND_ICON = "M10 2C7 6 4 9 4 12a6 6 0 0 0 12 0c0-3-3-6-6-10Z M7 12c0 2 1 3 3 3"
_LOGOUT_ICON = "M8 4H3v12h5 M7 10h10 m-3-3 3 3-3 3"


def _icon(shape: str) -> str:
    return ('<svg class="ui-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
            'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true" focusable="false"><path d="{shape}"/></svg>')


def _brand() -> str:
    return f'<span class="wordmark">{_icon(_BRAND_ICON)}SilentDeluge</span>'


def styles() -> str:
    """Return the local CSS shared by all pages."""
    return _CSS


def _html(title: str, shell: str, content: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{styles()}</style>
</head>
<body class="{shell}">
  <a class="skip-link" href="#main-content">Skip to main content</a>
{content}
</body>
</html>'''


def page(title: str, body_html: str) -> str:
    """Wrap trusted HTML for sign-in and messages. Callers supply the heading."""
    return _html(escape(title), "simple-shell", f'''
  <header class="site-bar"><div class="shell">{_brand()}</div></header>
  <main class="shell" id="main-content" tabindex="-1">
{body_html}
  </main>''')


def _dashboard(path: str, title: str, body: str, username: str | None, csrf: str | None) -> str:
    path = path.partition("?")[0]
    links = []
    for url, label, shape in _SECTIONS:
        if url == path:
            current = ' aria-current="page"'
        elif url != "/" and path.startswith(url + "/"):
            current = ' aria-current="location"'
        else:
            current = ""
        links.append(f'<li><a href="{url}"{current}>{_icon(shape)}<span>{label}</span></a></li>')
    account = ""
    if username is not None and csrf is not None:
        account = f'''
      <div class="dashboard-account">
        <p>Signed in as <strong>{escape(username)}</strong></p>
        <form method="post" action="/logout">
          <input type="hidden" name="csrf" value="{escape(csrf)}">
          <button type="submit">{_icon(_LOGOUT_ICON)}Log out</button>
        </form>
      </div>'''
    heading = escape(title)
    return _html(heading, "dashboard-shell", f'''
  <div class="dashboard-layout">
    <div class="dashboard-sidebar">
      <div class="dashboard-brand">{_brand()}</div>
      <details class="dashboard-menu">
        <summary aria-controls="dashboard-workspace">{_brand()} Menu</summary>
      </details>
      <div id="dashboard-workspace" class="dashboard-workspace">
        <nav class="dashboard-nav" aria-label="Workspace">
          <ul>{"".join(links)}</ul>
        </nav>{account}
      </div>
    </div>
    <main class="dashboard-main stack" id="main-content" tabindex="-1">
      <header class="dashboard-header"><h1>{heading}</h1></header>
{body}
    </main>
  </div>''')


def dashboard_page(path: str, title: str, body: str, username: str, csrf: str) -> str:
    """Render a signed-in dashboard with the sole h1. Pass trusted body HTML without an h1."""
    return _dashboard(path, title, body, username, csrf)


def status_badge(state: str) -> str:
    """Render a labeled inventory badge. An unsupported state raises KeyError."""
    label = _STATUS_LABELS[state]
    return f'<span class="status-badge" data-state="{state}">{label}</span>'


def patterns(username: str | None = None, csrf: str | None = None) -> str:
    """Render the dashboard reference using fixed synthetic evidence."""
    body = f'''
    <div class="patterns stack">
      <div class="stack pattern-intro">
        <p class="eyebrow">Local reference · Synthetic data</p>
        <p class="lede">Working examples for reading inventory, comparing evidence, and opening full records.</p>
        <nav class="section-nav" aria-label="On this page">
          <a href="#collection-states">Collection states</a>
          <a href="#inventory-evidence">Inventory evidence</a>
          <a href="#evidence-detail">Evidence detail</a>
          <a href="#paged-results">Paged results</a>
        </nav>
      </div>

      <section class="stack" aria-labelledby="collection-states">
        <header class="section-heading">
          <h2 id="collection-states" tabindex="-1">Collection states</h2>
          <p>Report coverage and freshness separately from the state of an instance.</p>
        </header>
        <div class="panel-grid">
          <article class="panel">
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
            <div class="filter-field">
              <label for="pattern-scope">Scope</label>
              <select id="pattern-scope">
                <option value="all">All scopes</option>
                <option value="main">Main stage</option>
                <option value="rehearsal">Rehearsal stage</option>
                <option value="storage">Storage stage</option>
              </select>
            </div>
          </div>
          <div class="summary">
            <p data-example-summary="all">Showing all 3 example instances.</p>
            <p data-example-summary="main" hidden>Showing 2 of 3 example instances for Main stage.</p>
            <p data-example-summary="rehearsal" hidden>Showing 1 of 3 example instances for Rehearsal stage.</p>
            <p data-example-summary="storage" hidden>No matching example instances. Choose All scopes to reset the filter.</p>
          </div>
          <div class="table-scroll" role="region" aria-labelledby="example-inventory-caption" tabindex="0">
            <table class="data-table">
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
                  <td>
                    <span class="table-preview">Listed in a complete scan of Main stage. Scope limited to the synthetic instance listing.</span>
                    <a class="detail-link" href="#paper-moon-evidence" aria-label="Full evidence for paper-moon">Full evidence</a>
                  </td>
                  <td><time datetime="2025-01-01T10:00:00Z">10:00 UTC</time></td>
                </tr>
                <tr data-example-scope="main">
                  <th scope="row">tin-piano</th>
                  <td>{status_badge("absent")}</td>
                  <td>
                    <span class="table-preview">Missing from a complete scan of Main stage. That absence applies only to this scope and observation time.</span>
                    <a class="detail-link" href="#tin-piano-evidence" aria-label="Full evidence for tin-piano">Full evidence</a>
                  </td>
                  <td><time datetime="2025-01-01T10:00:00Z">10:00 UTC</time></td>
                </tr>
                <tr data-example-scope="rehearsal">
                  <th scope="row">painted-sun</th>
                  <td>{status_badge("unknown")}</td>
                  <td>
                    <span class="table-preview">Missing from a partial capture of Rehearsal stage. The capture did not establish whether the instance was present.</span>
                    <a class="detail-link" href="#painted-sun-evidence" aria-label="Full evidence for painted-sun">Full evidence</a>
                  </td>
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
        <article class="panel" aria-labelledby="paper-moon-evidence">
          <h3 id="paper-moon-evidence" tabindex="-1">paper-moon</h3>
          <p>{status_badge("observed")} in Main stage</p>
          <p>Listed in a complete scan of Main stage. The stage sweep was scoped to the synthetic instance listing and does not describe any other stage.</p>
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
        </article>
        <article class="panel" aria-labelledby="tin-piano-evidence">
          <h3 id="tin-piano-evidence" tabindex="-1">tin-piano</h3>
          <p>{status_badge("absent")} in Main stage</p>
          <p>Missing from the complete synthetic instance scan. Absence applies only to Main stage at <time datetime="2025-01-01T10:00:00Z">10:00 UTC</time>.</p>
        </article>
        <article class="panel" aria-labelledby="painted-sun-evidence">
          <h3 id="painted-sun-evidence" tabindex="-1">painted-sun</h3>
          <p>{status_badge("unknown")} in Rehearsal stage</p>
          <p>Missing from the partial synthetic capture at <time datetime="2025-01-01T10:05:00Z">10:05 UTC</time>. It did not establish presence or absence.</p>
        </article>
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
    </div>'''
    return _dashboard("/patterns", "Inventory patterns", body, username, csrf)
