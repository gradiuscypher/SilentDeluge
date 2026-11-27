"""Render collector settings and effective access from the current account's workspace entries."""

from html import escape
from urllib.parse import urlencode


_TITLES = {
    '/collectors': 'Collectors',
    '/collectors/new': 'New collector',
    '/access': 'Access',
}
_PROVIDERS = {
    'linux': (
        'Linux', '/machines',
        '''{
  "scope": "example/guest",
  "asset": "example-guest",
  "packs": ["system"]
}''',
        'Run as an unprivileged account inside the enrolled guest. This example selects only '
        'the system pack: OS release metadata, uname, /proc/stat, and /proc/uptime. '
        'Approve each additional pack separately. For remote guests, import the resulting '
        'evidence as import-only rather than running on the inventory server.',
    ),
    'incus': (
        'Incus', '/incus',
        '''{
  "scope": "example/project",
  "snapshot_path": "/example/incus/snapshot.json"
}''',
        'Use a privately stored, approved instance export whose scope matches exactly. '
        'This template reads only a saved snapshot; it does not connect to Incus. '
        'Approve any live export or metrics capture separately using a dedicated, restricted identity.',
    ),
    'unifi': (
        'UniFi', '/unifi',
        '''{
  "scope": "example/site",
  "enabled": false,
  "base_url": "https://console.example.test",
  "site_id": "10000000-0000-4000-8000-000000000001",
  "api_key_env": "SILENTDELUGE_UNIFI_API_KEY"
}''',
        'Keep disabled until a dedicated read-only key and verified TLS are available for the '
        'approved console and single site. Approve both managed-device and connected-client lists. '
        'Optional device reads require separate approval. The environment variable name is a '
        'reference; supply the key only in the private collection environment.',
    ),
}


def _link(path, label, *, current=False):
    marker = ' aria-current="page"' if current else ''
    return f'<a href="{escape(path)}"{marker}>{escape(label)}</a>'


def _post_form(action, csrf, label, *, source):
    fields = (f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
              f'<input type="hidden" name="source" value="{escape(source)}">')
    return (f'<form action="{action}" method="post">{fields}'
            f'<button type="submit">{label}</button></form>')


def _collection_permission(entry):
    if entry['import_only']:
        return 'Import-only — no manual collection'
    return 'Granted' if entry['can_collect'] else 'Denied'


def _evidence_view(entry):
    return 'Enabled' if entry['enabled'] else 'Disabled'


def _collector_card(entry, csrf):
    source = entry['id']
    provider, class_path, _, _ = _PROVIDERS[entry['provider']]
    enabled = entry['enabled']
    if enabled:
        actions = _post_form('/disable', csrf, 'Disable evidence view', source=source)
        if entry['can_collect'] and not entry['import_only']:
            actions += _post_form('/preview', csrf, 'Preview one collection', source=source)
        actions += _link(class_path, f'View {provider} inventory')
    else:
        actions = _post_form('/enable', csrf, 'Enable evidence view', source=source)
    return f'''<article class="panel" id="collector-{escape(source)}">
      <h2>{escape(source)}</h2>
      <dl class="facts">
        <div><dt>Provider</dt><dd>{provider}</dd></div>
        <div><dt>Scope</dt><dd>{escape(entry['scope'])}</dd></div>
        <div><dt>Your evidence view</dt><dd>{_evidence_view(entry)}</dd></div>
        <div><dt>Manual collection</dt><dd>{_collection_permission(entry)}</dd></div>
      </dl>
      <p><strong>Configured read:</strong> {escape(entry['read'])}</p>
      <div class="section-nav">{actions}</div>
    </article>'''


def _collectors(entries, csrf):
    cards = ''.join(_collector_card(entry, csrf) for entry in entries)
    sources = f'<div class="panel-grid">{cards}</div>' if cards else '<p>No collectors are available in your scopes.</p>'
    return f'''
    <nav class="section-nav" aria-label="Collector settings">
      <a href="/collectors/new">New collector</a>
    </nav>
    <p>Enabling evidence does not collect it. Manual collection requires a grant, an enabled view, and confirmation.</p>
    {sources}'''


def _new_collector(query):
    selected = query.get('provider')
    options = ''.join(
        _link('/collectors/new?' + urlencode({'provider': provider}), details[0], current=provider == selected)
        for provider, details in _PROVIDERS.items()
    )
    preview = ''
    if selected is not None and selected not in _PROVIDERS:
        preview = '<p>Unknown provider. Choose one of the listed providers.</p>'
    elif selected is not None:
        provider, _, template, requirements = _PROVIDERS[selected]
        example = '<br>'.join(escape(line) for line in template.splitlines())
        preview = f'''<section class="panel" aria-labelledby="template-heading">
      <h2 id="template-heading">2. Preview the {provider} template</h2>
      <p>Synthetic example for the standalone collection runner, not a web allowlist entry.</p>
      <p><code>{example}</code></p>
      <h3>Read requirements</h3>
      <p>{requirements}</p>
      <p>Approve the exact scope and reads outside this workspace. Keep real settings and credentials private.</p>
    </section>'''
    return f'''
    <p><a href="/collectors">Back to collectors</a></p>
    <p>Examples only. Do not enter credentials here; this page does not configure collectors or grant access.</p>
    <section class="panel" aria-labelledby="provider-heading">
      <h2 id="provider-heading">1. Choose a provider</h2>
      <nav class="section-nav" aria-label="Provider templates">{options}</nav>
    </section>
    {preview}'''


def _scope_panels(entries):
    scopes = {}
    for entry in entries:
        scopes.setdefault(entry['scope'], []).append(entry)
    panels = []
    for scope, collectors in scopes.items():
        rows = ''.join(
            f'<tr><th scope="row">{_link("/collectors#collector-" + entry["id"], entry["id"])}</th>'
            f'<td>{_evidence_view(entry)}</td><td>{_collection_permission(entry)}</td></tr>'
            for entry in collectors
        )
        panels.append(f'''<div class="panel">
        <table class="data-table">
          <caption>View granted for scope: {escape(scope)}</caption>
          <thead><tr><th scope="col">Collector</th><th scope="col">Evidence view</th>
            <th scope="col">Manual collection</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>''')
    return ''.join(panels) if panels else '<p>No configured collectors are visible to this account.</p>'


def _access(entries):
    return f'''
    <section class="stack" aria-labelledby="effective-access">
      <h2 id="effective-access">Effective access now</h2>
      <p>The server uses exact scope grants, not project membership or named roles. A view grant lets you
        see the collector and enable or disable your own evidence view. Manual collection requires a separate
        grant and an enabled view. Import-only collectors never permit preview or collection.</p>
      {_scope_panels(entries)}
    </section>
    <section class="panel" aria-labelledby="proposed-access">
      <p class="eyebrow">Proposal only / Not enforced</p>
      <h2 id="proposed-access">Proposed project, group and collector ACL model</h2>
      <dl>
        <dt><strong>Project membership</strong></dt>
        <dd>Only members could receive access to a project's collectors. Membership alone would grant no operation.</dd>
        <dt><strong>Group roles</strong></dt>
        <dd>Project groups could receive a viewer role (view), operator role (view, preview and collect), or access
          administrator role (manage access, without implied evidence or collection access).</dd>
        <dt><strong>Per-collector ACLs</strong></dt>
        <dd>Grants would name a project member or group, an exact collector and explicit operations. Group roles
          would supply operation bundles, not access to every collector. Preview and collect would be checked separately.</dd>
        <dt><strong>Exclusions</strong></dt>
        <dd>No matching grant would mean deny. Any matching member or group exclusion would override grants for
          that collector and operation. Excluding view would also prevent preview and collect.</dd>
      </dl>
      <p>No project, group, role or ACL changes are available here.</p>
    </section>'''


def render(path: str, query: dict[str, str], entries: list[dict], username: str, csrf: str) -> str | None:
    """Render a settings page for already-authorized entries; unknown paths return None."""
    if path == '/collectors':
        content = _collectors(entries, csrf)
    elif path == '/collectors/new':
        content = _new_collector(query)
    elif path == '/access':
        content = _access(entries)
    else:
        return None
    from web_design import dashboard_page

    return dashboard_page(path, _TITLES[path], content, username, csrf)
