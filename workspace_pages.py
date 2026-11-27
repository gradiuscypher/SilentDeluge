"""Render private inventory workspaces from already scope-filtered entries."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from html import escape
from json import dumps
import re
from urllib.parse import urlencode

from inventory import compare
from web_design import status_badge


_WORKSPACES = {
    '/machines': ('linux', 'Machines', 'Browse operating system, software, resource, and link evidence.'),
    '/incus': ('incus', 'Incus', 'Browse instance inventory, runtime sightings, and declaration comparisons.'),
    '/unifi': ('unifi', 'UniFi', 'Browse managed devices and connected-client sightings by site.'),
}
_PATHS = {provider: path for path, (provider, _, _) in _WORKSPACES.items()}
_SETTINGS = {'/collectors', '/collectors/new', '/access'}
_SECTIONS = {
    'linux': {
        'system': ('System', ''),
        'packages': ('Installed packages', ''),
        'services': ('Services', 'Service coverage depends on the reporting manager.'),
        'cpu': ('CPU usage', 'Sampled usage in the reported accounting view, not CPU capacity.'),
        'addresses': ('Network addresses', 'Addresses observed in the machine network namespace.'),
        'memory': ('Memory', 'Usage and capacity in the reported accounting view.'),
        'disk': ('Root filesystem', 'Space usage for the root filesystem, not all guest storage.'),
        'storage': ('Scoped storage', 'Usage at selected mount points, not all guest storage.'),
        'users': ('Local accounts', ''),
        'interfaces': ('Network interfaces', 'Interfaces in the observed network namespace.'),
        'network_links': ('Network links', 'Counters in the observed network namespace.'),
    },
    'incus': {
        'instances': ('Incus instances', ''),
        'runtime': ('Runtime sightings', 'Runtime sightings do not cover the instance inventory.'),
        'guest_addresses': ('Guest addresses', 'Address sightings do not establish absence.'),
        'comparison': ('Declaration comparison', 'Only a current complete instance capture can establish absence.'),
    },
    'unifi': {
        'managed_devices': ('Managed devices', ''),
        'connected_clients': ('Connected clients', 'A client not listed here may be disconnected; absence is not established.'),
    },
}

_DEFAULT_SECTIONS = {'linux': 'overview', 'incus': 'instances', 'unifi': 'managed_devices'}
_TABLE_FIELDS = {
    ('linux', 'system'): ('os_id', 'os_version', 'kernel_release', 'architecture'),
    ('linux', 'packages'): ('version', 'architecture', 'manager'),
    ('linux', 'services'): ('state', 'manager'),
    ('linux', 'cpu'): ('busy_percent', 'cpu_cores_used', 'sample_seconds'),
    ('linux', 'memory'): ('used_bytes', 'total_bytes', 'limit_bytes'),
    ('linux', 'disk'): ('used_bytes', 'total_bytes', 'available_bytes'),
    ('linux', 'storage'): ('filesystem', 'used_bytes', 'total_bytes', 'available_bytes'),
    ('linux', 'users'): ('uid', 'gid'),
    ('linux', 'interfaces'): ('ifindex',),
    ('linux', 'network_links'): ('operstate', 'mtu_bytes', 'rx_bytes', 'tx_bytes'),
    ('linux', 'addresses'): ('ip_address',),
    ('incus', 'instances'): ('kind', 'state', 'cpu', 'memory', 'location'),
    ('incus', 'runtime'): ('kind', 'state'),
    ('incus', 'guest_addresses'): ('ip_address',),
    ('incus', 'comparison'): ('Differing fields',),
    ('unifi', 'managed_devices'): ('model', 'state', 'ip_address', 'firmware_version'),
    ('unifi', 'connected_clients'): ('type', 'ip_address', 'mac_address', 'connected_at'),
}
_IDENTITY_HEADERS = {
    ('incus', 'instances'): 'Instance', ('incus', 'comparison'): 'Instance',
    ('incus', 'runtime'): 'Instance', ('incus', 'guest_addresses'): 'Instance',
    ('unifi', 'managed_devices'): 'Device', ('unifi', 'connected_clients'): 'Client',
    ('linux', 'packages'): 'Package', ('linux', 'services'): 'Service',
}


def _e(value):
    return escape(_html_text(value), quote=True)


def _html_text(value):
    # JSON strings can contain isolated surrogate escapes; HTML must be UTF-8.
    return str(value).encode('utf-8', errors='replace').decode('utf-8')


def _url(path, **params):
    query = urlencode([(key, value) for key, value in params.items() if value not in (None, '')], errors='replace')
    return f'{path}?{query}' if query else path


def _link(url, label):
    return f'<a href="{_e(url)}">{_e(label)}</a>'


def _time(value):
    return f'<time datetime="{_e(value)}">{_e(value)}</time>'


def _document(path, title, body, username, csrf):
    from web_design import dashboard_page

    return dashboard_page(path, title, body, username, csrf)


def _enabled(entries):
    return {entry['id']: entry for entry in entries if entry['enabled']}


def _latest(timestamps):
    return max(timestamps, key=datetime.fromisoformat, default=None)


def _overview(entries):
    cards = []
    for path, (provider, title, _) in _WORKSPACES.items():
        counts = Counter()
        attempts, captures = [], []
        for entry in entries:
            if entry['provider'] != provider:
                continue
            counts['sources'] += 1
            if not entry['enabled']:
                counts['disabled'] += 1
                continue
            state = entry['state']
            if state is None:
                counts['without_attempt'] += 1
                continue
            attempts.append(state['timestamp'])
            for name, history in state['sections'].items():
                for kind in ('complete', 'partial'):
                    if capture := history.get(kind):
                        captures.append(capture['timestamp'])
                outcome = state['results'].get(name, {}).get('outcome')
                if outcome == 'complete':
                    counts['complete'] += 1
                elif outcome == 'partial':
                    counts['partial'] += 1
                else:
                    counts['not_current'] += 1
        attempt, capture = _latest(attempts), _latest(captures)
        cards.append(f'<section class="panel"><h2>{_link(path, title)}</h2>'
                     f'<p>Sources: {counts["sources"]} · Disabled: {counts["disabled"]} · '
                     f'Enabled without an attempt: {counts["without_attempt"]}</p>'
                     f'<p>Sections: {counts["complete"]} complete · {counts["partial"]} partial · '
                     f'{counts["not_current"]} without a current capture</p>'
                     '<p>Latest processed attempt: ' + (_time(attempt) if attempt else 'None') + '</p>'
                     '<p>Newest retained capture: ' + (_time(capture) if capture else 'None') + '</p></section>')
    empty = '<p>No sources are available in your scopes.</p>' if not entries else ''
    return empty + '<div class="panel-grid">' + ''.join(cards) + '</div>'


def render(path, query, entries, username, csrf):
    """Return complete HTML for a workspace route, or None for an unknown path."""
    if path in _SETTINGS:
        import settings_pages

        return settings_pages.render(path, query, entries, username, csrf)
    if path == '/':
        title, body = 'Inventory overview', _overview(entries)
    elif path == '/relationships':
        import asset_relations

        title = 'Relationships'
        entry = _enabled(entries).get(query.get('source'))
        link = ''
        if entry and (target := _PATHS.get(entry['provider'])):
            link = '<p>' + _link(_url(target, source=entry['id']), 'Browse source inventory') + '</p>'
        body = link + asset_relations.render(entries, query)
    elif path in _WORKSPACES:
        _, title, _ = _WORKSPACES[path]
        body = _inventory(path, query, _enabled(entries))
    elif path.endswith('/detail') and (workspace := path.removesuffix('/detail')) in _WORKSPACES:
        detail = _detail(workspace, query, _enabled(entries))
        if detail is None:
            return None
        title, body = detail
    else:
        return None
    return _document(path, title, body, username, csrf)


_COVERAGE = {
    'complete': 'Complete', 'partial': 'Partial', 'stale': 'Stale',
    'failed': 'Failed', 'unknown': 'Unknown', 'not-connected': 'Not connected',
}
_COMPARISON = {
    'match': 'Match', 'disagreement': 'Disagreement', 'absent': 'Absent',
    'observed-and-undeclared': 'Observed and undeclared',
    'stale': 'Stale', 'unknown': 'Unknown', 'not-connected': 'Not connected',
}
_REPORTED_STATES = {
    'linux': ('Down', 'Dormant', 'Installed', 'Linked', 'LowerLayerDown', 'Masked',
              'NotPresent', 'Started', 'Testing', 'Up'),
    'incus': ('Error', 'Frozen', 'Running', 'Stopped'),
    'unifi': ('Adopting', 'Disconnected', 'Getting Ready', 'Isolated', 'Offline', 'Online',
              'Pending Adoption', 'Updating'),
}
_FIELD_LABELS = {
    'cpu': 'CPU limit (count or pin set)', 'memory': 'Memory limit (as reported)',
    'sample_seconds': 'Sample duration (s)', 'busy_percent': 'Busy CPU time (%)',
    'cpu_cores_used': 'Average CPU usage (cores)', 'total_bytes': 'Total (bytes)',
    'used_bytes': 'Used (bytes)', 'available_bytes': 'Available (bytes)',
    'limit_bytes': 'Limit (bytes)', 'mac_address': 'MAC address', 'ip_address': 'IP address',
    'cpu_seconds_total': 'CPU time counters (s)', 'cpu_index': 'CPU index',
    'poe': 'Power over Ethernet', 'mtu_bytes': 'MTU (bytes)', 'uid': 'UID', 'gid': 'GID', 'id': 'ID',
    'os_id': 'OS', 'os_version': 'OS version', 'kernel_release': 'Kernel',
    'operstate': 'Link state', 'ifindex': 'Interface index', 'rx_bytes': 'Received (bytes)',
    'tx_bytes': 'Sent (bytes)', 'firmware_version': 'Firmware', 'Differing fields': 'Differing fields',
}


def _label(field):
    return _FIELD_LABELS.get(field) or field.replace('_', ' ').strip().capitalize() or 'Unnamed field'


def _value(value, field=None):
    if isinstance(value, dict):
        if not value:
            return 'No fields reported'
        return '<dl class="facts">' + ''.join(
            f'<div><dt>{_e(_label(key))}</dt><dd>{_value(child, key)}</dd></div>'
            for key, child in value.items()
        ) + '</dl>'
    if isinstance(value, list):
        if not value:
            return 'None listed'
        return '<ul>' + ''.join(f'<li>{_value(child)}</li>' for child in value) + '</ul>'
    if value is None:
        return 'Uncapped' if field == 'limit_bytes' else 'Not reported'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if value == '':
        return 'Empty text'
    if isinstance(value, str) and (field == 'timestamp' or field == 'observed_at' or (field or '').endswith('_at')):
        return _time(value)
    return _e(value)


def _search_values(value):
    if isinstance(value, dict):
        for field, child in value.items():
            yield _label(field)
            if isinstance(child, bool) or child is None:
                yield _value(child, field)
            yield from _search_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _search_values(child)
    elif value is not None:
        yield str(value)


def _section_info(provider, name):
    return _SECTIONS[provider].get(name, (_label(name), ''))


def _section_names(entry):
    state = entry['state']
    names = (state['sections'].keys() | state['results'].keys()) if state else set()
    if entry['provider'] == 'incus':
        names.discard('comparison')
        if entry['declarations'] is not None:
            names.add('comparison')
    return names


@dataclass(frozen=True)
class _Section:
    entry: dict
    name: str
    result: dict
    complete: dict | None
    partial: dict | None

    @classmethod
    def from_entry(cls, entry, name):
        state = entry['state']
        raw_name = 'instances' if entry['provider'] == 'incus' and name == 'comparison' else name
        history = state['sections'].get(raw_name, {}) if state else {}
        result = state['results'].get(raw_name, {}) if state else {}
        return cls(entry, name, result, history.get('complete'), history.get('partial'))

    @property
    def outcome(self):
        return self.result.get('outcome', 'not returned')

    @property
    def status(self):
        if self.entry['state'] is None:
            return 'not-connected'
        return self.outcome if self.outcome in _COVERAGE else 'unknown'

    @property
    def current_partial(self):
        return self.outcome == 'partial' and self.partial and self.partial['timestamp'] == self.result['timestamp']


@dataclass(frozen=True)
class _Row:
    section: _Section
    basis: str
    index: int
    status: str
    title: str
    item: str
    record: dict

    @property
    def capture(self):
        return self.section.partial if self.basis == 'partial' else self.section.complete


def _comparison(section):
    entry, complete = section.entry, section.complete
    scans = []
    if complete:
        # The evidence store holds normalized records, not the offline scan shape.
        instances = [
            dict(name=row['name'], type=row['kind'], status=row['state'],
                 expanded_config={'limits.cpu': row.get('cpu'), 'limits.memory': row.get('memory')})
            for row in complete['records']
        ]
        scans.append(dict(source=entry['id'], scope=entry['scope'], timestamp=complete['timestamp'],
                          outcome='complete', instances=instances))
    for index, result in enumerate(compare(entry['declarations'], scans)):
        status = result['status'].replace(' ', '-')
        # A later incomplete, omitted, or replayed section cannot establish absence.
        if entry['state'] is not None and section.outcome != 'complete':
            status = 'stale' if result['observed'] is not None else 'unknown'
        fields = {
            'Declared': result['declared'],
            'Observed': result['observed'],
            'Differing fields': [_label(field) for field in result['differences']],
        }
        yield _Row(section, 'comparison', index, status, result['name'], result['name'], fields)


def _record_row(section, basis, index, record):
    key = next((key for key in ('name', 'path', 'view', 'asset', 'mac_address', 'ip_address', 'id')
                if isinstance(record.get(key), str) and record[key]), None)
    title = record[key] if key else 'Observation'
    item = record.get('id') if section.entry['provider'] == 'unifi' else None
    current = section.outcome == 'complete' if basis == 'complete' else section.current_partial
    return _Row(section, basis, index, basis if current else 'stale', title,
                item if isinstance(item, str) and item else title, record)


def _rows(section):
    if section.entry['provider'] == 'incus' and section.name == 'comparison':
        if section.entry['declarations'] is not None:
            yield from _comparison(section)
        return

    for basis, capture in (('complete', section.complete), ('partial', section.partial)):
        if capture:
            for index, record in enumerate(capture['records']):
                yield _record_row(section, basis, index, record)


def _digest(value):
    return sha256(dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()


def _section_param(name):
    # Provider extensions need not fit the HTTP query-value limit.
    if (name == 'overview' or name.startswith('@') or not name.isprintable()
            or len(urlencode({'section': name})) > 264):
        return '@' + _digest(name)
    return name


def _source_identity(entry):
    return entry['id'], entry['provider'], entry['scope']


def _record_digest(row):
    entry = row.section.entry
    declaration = entry['declarations'] if row.basis == 'comparison' else None
    return _digest((_source_identity(entry), row.section.name, row.basis, row.index,
                    row.capture['timestamp'] if row.capture else None, row.record,
                    (declaration['source'], declaration['timestamp']) if declaration else None))


def _record_url(path, row):
    # A capture-local index distinguishes identical observations. Verify the digest
    # before displaying anything: an index, name, or provider ID alone can be reused.
    item = f'{_digest(row.section.name)}.{row.basis}.{row.index}.{_record_digest(row)}'
    return _url(f'{path}/detail', source=row.section.entry['id'], item=item)


_RECORD_ITEM = re.compile(r'([0-9a-f]{64})\.(complete|partial|comparison)\.(0|[1-9][0-9]{0,15})\.([0-9a-f]{64})')


def _find_record(entry, item):
    if not (match := _RECORD_ITEM.fullmatch(item)):
        return None
    section_key, basis, index, digest = match.groups()
    names = [name for name in _section_names(entry) if _digest(name) == section_key]
    if len(names) != 1:
        return None
    section = _Section.from_entry(entry, names[0])
    index = int(index)
    if entry['provider'] == 'incus' and section.name == 'comparison':
        if basis != 'comparison':
            return None
        row = next((row for row in _comparison(section) if row.index == index), None)
    elif basis == 'comparison':
        return None
    else:
        capture = section.complete if basis == 'complete' else section.partial
        if not capture or index >= len(capture['records']):
            return None
        row = _record_row(section, basis, index, capture['records'][index])
    return row if row is not None and _record_digest(row) == digest else None


def _attempt(entry):
    state = entry['state']
    if state is None:
        text = 'no offline evidence imported' if entry['import_only'] else 'no collection attempted'
        return f'<p>Not connected: {text}.</p>'
    label = 'Latest processed offline envelope' if state.get('imported') else 'Latest collection'
    return f'<p>{label}: {_e(state["outcome"])} — {_time(state["timestamp"])}.</p>'


def _section_context(section, description):
    entry = section.entry
    body = f'<p>Source: {_e(entry["id"])} · Scope: {_e(entry["scope"])}</p>'
    if description:
        body += f'<p>{_e(description)}</p>'
    if entry['provider'] == 'incus' and section.name == 'comparison':
        declaration = entry['declarations']
        body += f'<p>Declarations: {_e(declaration["source"])} — {_time(declaration["timestamp"])}.</p>'
    body += _attempt(entry)
    when = ' — ' + _time(section.result['timestamp']) if section.result else ''
    body += f'<p>Latest section outcome: {_e(section.outcome)}{when}.</p>'
    if section.outcome != 'complete':
        body += '<p>Presence of omitted records is unknown.</p>'
    for basis, capture, current in (('complete', section.complete, section.outcome == 'complete'),
                                    ('partial', section.partial, section.current_partial)):
        if capture:
            label = (('Complete capture' if current else 'Stale — last verified complete capture')
                     if basis == 'complete' else ('Partial sightings' if current else 'Stale — retained partial sightings'))
            count = len(capture['records'])
            body += f'<p>{label}: {_time(capture["timestamp"])} — {count} {"record" if count == 1 else "records"}.</p>'
    if section.outcome == 'partial' and not section.current_partial:
        body += '<p>No sightings in this partial capture (0 records).</p>'
    if not section.complete and not section.partial and section.outcome == 'failed':
        body += '<p>No retained capture.</p>'
    return body


def _section_summary(section):
    entry = section.entry
    title, _ = _section_info(entry['provider'], section.name)
    source = entry['id']
    links = [_link(_url(_PATHS[entry['provider']], source=source), 'Browse source'),
             _link(_url('/relationships', source=source), 'Source relationships')]
    if entry['declarations'] is not None and section.name != 'comparison':
        links.append(_link(_url('/incus', source=source, section='comparison'), 'Declaration comparison'))
    return _disclose(f'{source} — {title} ({_COVERAGE[section.status]})',
                     _section_context(section, '') + _detail_links(links, 'Source context'))


def _related(enabled):
    incus = defaultdict(list)
    machines = defaultdict(list)
    for entry in enabled.values():
        placement = entry.get('placement')
        if entry['provider'] == 'incus':
            incus[entry['scope']].append(entry)
        elif entry['provider'] == 'linux' and placement:
            machines[(placement['scope'], placement['name'])].append(entry)
    for group in (incus, machines):
        for sources in group.values():
            sources.sort(key=lambda entry: entry['id'])
    return incus, machines


def _placement_links(entry, item, related):
    incus, machines = related
    if entry['provider'] == 'linux' and (placement := entry.get('placement')):
        return [_link(_url('/incus', source=target['id'], section='instances', item=placement['name']),
                      f'Configured Incus placement: {placement["name"]} ({target["id"]})')
                for target in incus.get(placement['scope'], ())]
    if entry['provider'] == 'incus' and item:
        return [_link(_url('/machines', source=target['id']), f'Machine with configured placement: {target["id"]}')
                for target in machines.get((entry['scope'], item), ())]
    return []


def _badge(status, labels=_COVERAGE):
    if status in ('complete', 'partial', 'stale', 'absent', 'unknown'):
        return status_badge(status)
    return f'<span class="status-badge">{_e(labels[status])}</span>'


def _disclose(label, body):
    return f'<details><summary>{_e(label)}</summary>{body}</details>' if body else ''


def _detail_links(links, label):
    return f'<nav class="section-nav" aria-label="{label}">{"".join(links)}</nav>' if links else ''


def _result_table(caption, headings, rows):
    columns = ''.join(f'<th scope="col">{_e(heading)}</th>' for heading in headings)
    return ('<div class="table-scroll" role="region" aria-labelledby="inventory-caption" tabindex="0">'
            '<table class="data-table"><caption id="inventory-caption">'
            f'{_e(caption)}</caption><thead><tr>{columns}</tr></thead><tbody>'
            + ''.join(f'<tr><th scope="row">{identity}</th>'
                      + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>'
                      for identity, *cells in rows)
            + '</tbody></table></div>')


def _short(value):
    text = ' '.join(str(value).split())
    return text if len(text) <= 48 else text[:47].rstrip() + '…'


def _cell(line, second=None):
    markup = f'<div class="cell-summary">{line}</div>'
    return markup if second is None else markup + f'<div class="cell-summary">{second}</div>'


def _field_cell(record, field):
    if field not in record:
        return _cell('—')
    value = record[field]
    if field == 'Differing fields':
        return _cell(_e(value[0]) if value else 'None',
                     f'<small>+{len(value) - 1} more</small>' if len(value) > 1 else None)
    if isinstance(value, (dict, list)):
        count = len(value)
        noun = 'field' if isinstance(value, dict) else 'item'
        return _cell(f'{count} {noun}{"s" if count != 1 else ""}')
    if type(value) in (str, int, float) and value != '':
        shortened = _short(value)
        if shortened != str(value):
            return _cell(_e(shortened or 'Whitespace'))
    return _cell(_value(value, field))


def _row_badge(row):
    return _badge(row.status, _COMPARISON if row.basis == 'comparison' else _COVERAGE)


def _row_cells(path, row, fields):
    entry, name = row.section.entry, row.section.name
    source = entry['id']
    identity = _cell(_link(_record_url(path, row), _short(row.title) or 'Observation'))
    retained = '<small>Retained partial</small>' if row.basis == 'partial' and row.status == 'stale' else None
    context = _cell(_link(_url(path, source=source, section=_section_param(name)), _short(source)),
                    f'<small>{_e(_short(entry["scope"]))}</small>')
    captured = _time(row.capture['timestamp']) if row.capture else '—'
    return (identity, *(_field_cell(row.record, field) for field in fields),
            _cell(_row_badge(row), retained), context, _cell(captured))


def _detail(path, query, enabled):
    if query.keys() != {'source', 'item'}:
        return None
    entry = enabled.get(query['source'])
    if not entry or entry['provider'] != _WORKSPACES[path][0]:
        return None
    item = query['item']
    if path == '/machines' and item == _machine_item(entry):
        sections = _machine_sections(entry)
        links = [_link(_url(path, source=entry['id']), 'Back to machine overview')]
        links += _machine_links(entry, sections, _related(enabled))
        context = f'<p>Source: {_e(entry["id"])} · Scope: {_e(entry["scope"])}</p>' + _attempt(entry)
        if sections:
            context += ('<h2>Section coverage</h2>'
                        + ''.join(_section_summary(section) for section in sections.values()))
        else:
            context += '<p>No inventory sections received.</p>'
        return entry['id'], _detail_links(links, 'Machine evidence') + f'<article class="panel">{context}</article>'

    if (row := _find_record(entry, item)) is None:
        return None
    section = row.section
    source = entry['id']
    title, description = _section_info(entry['provider'], section.name)
    links = [_link(_url(path, source=source, section=_section_param(section.name)), f'Back to {title}'),
             _link(_url('/relationships', source=source), 'Source relationships')]
    links += _placement_links(entry, row.item, _related(enabled))
    if entry['declarations'] is not None and section.name != 'comparison':
        links.append(_link(_url(path, source=source, section='comparison'), 'Declaration comparison'))
    if row.basis == 'comparison':
        basis = 'Complete instance capture' if row.capture else 'No complete instance capture'
    elif row.basis == 'partial':
        basis = 'Retained partial sighting' if row.status == 'stale' else 'Partial sighting'
    else:
        basis = 'Last verified complete capture' if row.status == 'stale' else 'Complete capture'
    captured = ' — ' + _time(row.capture['timestamp']) if row.capture else ''
    note = ('<p>The latest attempt did not verify current presence or absence.</p>'
            if row.basis == 'comparison' and row.status in ('stale', 'unknown') else '')
    body = (_detail_links(links, 'Record context')
            + f'<article class="record-detail panel"><h2>{_e(title)}</h2>'
              f'<p>{_row_badge(row)} {basis}{captured}</p>{note}'
              f'{_section_context(section, description)}'
              f'<h2>Record fields</h2>{_value(row.record)}</article>')
    return _html_text(row.title) if row.title.strip() else 'Observation', body


def _matches(tokens, *values):
    text = ' '.join(values).casefold()
    return all(token in text for token in tokens)


def _machine_sections(entry):
    return {name: _Section.from_entry(entry, name) for name in sorted(_section_names(entry))}


def _machine_statuses(entry, sections):
    if not sections:
        return Counter({'unknown' if entry['state'] else 'not-connected': 1})
    counts = Counter(section.status for section in sections.values())
    counts['stale'] = sum(bool((section.complete and section.outcome != 'complete')
                               or (section.partial and not section.current_partial))
                          for section in sections.values())
    return counts


def _sample(section):
    if not section:
        return None
    # Prefer the current capture; only fall back to an older retained value.
    captures = ((section.complete, 'complete', section.outcome == 'complete'),
                (section.partial, 'partial', bool(section.current_partial)))
    available = [(capture, basis, current) for capture, basis, current in captures
                 if capture and capture['records']]
    if not available:
        return None
    capture, basis, _ = max(available, key=lambda pair: (pair[2], datetime.fromisoformat(pair[0]['timestamp'])))
    return _record_row(section, basis, 0, capture['records'][0])


def _size(value):
    if not 0 <= value < 1024**6:
        return f'{_short(value)} B'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if value < 1024 or unit == 'PiB':
            return f'{value:.3g} {unit}'
        value /= 1024


def _machine_rows(sources, status, item, tokens):
    for entry in sources:
        if item and item != entry['id']:
            continue
        sections = _machine_sections(entry)
        counts = _machine_statuses(entry, sections)
        if status and not counts[status]:
            continue
        samples = {name: _sample(sections.get(name)) for name in ('system', 'cpu', 'memory')}
        if tokens:
            assets = (sample.record['asset'] for section in sections.values()
                      if (sample := _sample(section)) and isinstance(sample.record.get('asset'), str))
            facts = (value for sample in samples.values() if sample for value in _search_values(sample.record))
            if not _matches(tokens, entry['id'], entry['scope'], *assets, *facts):
                continue
        yield entry, sections, counts, samples


def _machine_item(entry):
    return 'machine.' + _digest(_source_identity(entry))


def _machine_links(entry, sections, related):
    source = entry['id']
    links = [_link(_url('/machines', source=source, section=_section_param(name)), _section_info('linux', name)[0])
             for name in sections]
    links.append(_link(_url('/relationships', source=source), 'Source relationships'))
    return links + _placement_links(entry, '', related)


def _machine_cells(machine):
    entry, _, counts, samples = machine
    source = entry['id']
    system, cpu, memory = (samples[name].record if samples[name] else {} for name in ('system', 'cpu', 'memory'))

    def field(value, name):
        sample = samples[name]
        if not value or sample is None:
            return _cell('—')
        if isinstance(value, (dict, list)):
            value = 'Structured value'
        status = _badge(sample.status) if sample.status != 'complete' else None
        return _cell(_link(_record_url('/machines', sample), _short(value)), status)

    os_release = ' '.join(str(system[key]) for key in ('os_id', 'os_version')
                          if system.get(key) and isinstance(system[key], (str, int, float)))
    cpu_value = ''
    for key, units in (('busy_percent', '%'), ('cpu_cores_used', ' cores')):
        number = cpu.get(key)
        if type(number) in (float, int):
            cpu_value = (f'{number:.3g}' if type(number) is float else _short(number)) + units
            break
    memory_value = ''
    if type(memory.get('used_bytes')) is int:
        memory_value = _size(memory['used_bytes'])
        capacity = memory.get('total_bytes', memory.get('limit_bytes'))
        if type(capacity) is int:
            memory_value += ' / ' + _size(capacity)
        elif 'limit_bytes' in memory and capacity is None:
            memory_value += ' / uncapped'
    primary = next(key for key in ('failed', 'stale', 'partial', 'unknown', 'not-connected', 'complete') if counts[key])
    other = sum(bool(count) for key, count in counts.items() if key != primary)
    evidence = _cell(_badge(primary), f'<small>+{other} other {"status" if other == 1 else "statuses"}</small>'
                     if other else None)
    attempt = _time(entry['state']['timestamp']) if entry['state'] else '—'
    return (_cell(_link(_url('/machines/detail', source=source, item=_machine_item(entry)), _short(source))),
            _cell(_e(_short(entry['scope']))), field(os_release, 'system'),
            field(system.get('kernel_release'), 'system'), field(cpu_value, 'cpu'), field(memory_value, 'memory'),
            evidence, _cell(attempt))


_MACHINE_HEADINGS = ('Machine', 'Scope', 'OS', 'Kernel', 'CPU usage', 'Memory used',
                     'Evidence', 'Latest attempt')


def _section_links(path, provider, sections, active, params):
    if provider == 'linux':
        return ''
    common = {key: value for key, value in params.items() if key in ('source', 'q', 'per_page')}
    links = []
    for name, label in sections.items():
        current = ' aria-current="page"' if name == active else ''
        links.append(f'<a href="{_e(_url(path, **common, section=name))}"{current}>{_e(_short(label))}</a>')
    return f'<nav class="section-nav" aria-label="Inventory sections">{"".join(links)}</nav>'


def _empty_inventory(provider, sources, source, section, sections, filtered):
    if not sources:
        if source:
            return '<p>No matching enabled source.</p>'
        return ('<p>No enabled sources in this workspace. '
                'Enable an approved view in <a href="/collectors">Collectors</a>.</p>')
    if section not in sections:
        if provider == 'incus' and section == 'comparison':
            return '<p>No declarations configured for these enabled sources.</p>'
        return '<p>Section unavailable.</p>'
    if filtered:
        return '<p>No matching records. The filter does not establish absence.</p>'
    return '<p>No records to display. Check source and section coverage.</p>'


def _select(name, label, choices, current):
    options = ''.join(f'<option value="{_e(value)}"' + (' selected' if value == current else '')
                      + f'>{_e(text)}</option>' for value, text in choices)
    return f'<label>{label} <select name="{name}">{options}</select></label>'


def _filters(path, sources, sections, statuses, params):
    source_choices = [('', 'All enabled sources')] + [(entry['id'], entry['id']) for entry in sources]
    section_choices = [(key, _short(label)) for key, label in sections.items()]
    status_choices = [('', 'Any status')] + list(statuses.items())
    item = (f'<input type="hidden" name="item" value="{_e(params["item"])}">' if params.get('item') else '')
    return (f'<form method="get" action="{path}" class="report-header toolbar filters" aria-label="Filter inventory">'
            f'{item}<label>Search <input type="search" name="q" value="{_e(params.get("q", ""))}"></label>'
            f'{_select("source", "Source", source_choices, params.get("source", ""))}'
            f'{_select("section", "Section", section_choices, params.get("section", ""))}'
            f'{_select("status", "Status", status_choices, params.get("status", ""))}'
            f'{_select("per_page", "Per page", [("25", "25"), ("50", "50")], params.get("per_page", "25"))}'
            '<button type="submit">Apply</button>'
            f'{_link(path, "Clear filters")}</form>')


def _paginate(path, items, per_page, raw_page, params):
    total = len(items)
    if not total:
        return [], '<p class="summary">0 results.</p>', ''
    last = (total - 1) // per_page + 1
    number = 1
    if raw_page.isascii() and raw_page.isdecimal():
        digits = raw_page.lstrip('0')
        number = min(last, max(1, int(digits or '0'))) if len(digits) < 10 else last
    start, end = (number - 1) * per_page, min(number * per_page, total)
    label = f'<p class="summary">Showing {start + 1}–{end} of {total} results · Page {number} of {last}.</p>'
    links = []
    if number > 1:
        links.append(_link(_url(path, **params, page=number - 1), 'Previous page'))
    if number < last:
        links.append(_link(_url(path, **params, page=number + 1), 'Next page'))
    pagination = '<nav class="pagination" aria-label="Pagination">' + ''.join(links) + '</nav>' if links else ''
    return items[start:end], label, pagination


def _matching_rows(provider, sections, status, item, tokens):
    for section in sections:
        title, _ = _section_info(provider, section.name)
        coverage = _COMPARISON if provider == 'incus' and section.name == 'comparison' else _COVERAGE
        for row in _rows(section):
            if item and item != row.item:
                continue
            if status and status != row.status:
                state = row.record.get('state', row.record.get('operstate'))
                if status in coverage or not isinstance(state, str) or status != _state_key(state):
                    continue
            entry = section.entry
            if tokens and not _matches(tokens, entry['id'], entry['scope'], title,
                                       row.title, row.item, *_search_values(row.record)):
                continue
            yield row


def _state_key(value):
    return value.casefold().replace(' ', '_')


def _inventory(path, query, enabled):
    provider, _, _ = _WORKSPACES[path]
    all_sources = sorted((entry for entry in enabled.values() if entry['provider'] == provider),
                         key=lambda entry: (entry['id'].casefold(), entry['id']))
    source = query.get('source', '')
    sources = [entry for entry in all_sources if not source or entry['id'] == source]
    q = query.get('q', '').strip()
    default = _DEFAULT_SECTIONS[provider]
    section_key = query.get('section') or default
    # Relationship drilldowns pass the canonical UniFi client ID as a search term.
    if provider == 'unifi' and source and not query.get('section') and q.startswith('unifi:'):
        if ':connected_clients:' in q:
            section_key = 'connected_clients'
    item = query.get('item', '')
    status = _state_key(query.get('status', ''))
    per_page = 50 if query.get('per_page') == '50' else 25
    params = {'per_page': '50'} if per_page == 50 else {}
    names = set(_SECTIONS[provider]) - {'comparison'}
    for entry in sources:
        names.update(_section_names(entry))
    if provider != 'linux':
        names.discard(default)
    section_names = {default: default}
    section_names.update((_section_param(name), name) for name in sorted(names))
    sections = {key: 'Machine overview' if provider == 'linux' and key == default else _section_info(provider, name)[0]
                for key, name in section_names.items()}
    section = section_names.get(section_key, section_key)
    if source and sources:
        params['source'] = source
    if section_key in sections:
        params['section'] = section_key
    if q and sources:
        params['q'] = q

    machines = provider == 'linux' and section_key == default
    comparison = provider == 'incus' and section == 'comparison'
    statuses = dict(_COMPARISON if comparison else _COVERAGE)
    if not machines and not comparison:
        statuses.update((_state_key(state), f'Reported state: {state}')
                        for state in _REPORTED_STATES[provider])
    tokens = q.casefold().split()
    evidence = ''
    observed = []
    if machines:
        rows = list(_machine_rows(sources, status, item, tokens))
    elif section_key in sections:
        observed = [_Section.from_entry(entry, section) for entry in sources
                    if not comparison or entry['declarations'] is not None]
        rows = list(_matching_rows(provider, observed, status, item, tokens))
        rows.sort(key=lambda row: (row.title.casefold(), row.title, row.section.entry['id'], row.basis, row.item))
        if status and status not in statuses and rows:
            record = rows[0].record
            statuses[status] = f'Reported state: {record.get("state", record.get("operstate"))}'
    else:
        rows = []
    if status in statuses and sources:
        params['status'] = status
    if item and rows:
        params['item'] = item
    visible, count, pagination = _paginate(path, rows, per_page, query.get('page', ''), params)

    caption, description = _section_info(provider, section) if section_key in sections else (sections[default], '')
    if machines:
        table = _result_table('Machine overview', _MACHINE_HEADINGS, map(_machine_cells, visible))
        if source and visible:
            entry, machine_sections, _, _ = visible[0]
            evidence = _detail_links(_machine_links(entry, machine_sections, _related(enabled)), 'Machine evidence')
    else:
        fields = _TABLE_FIELDS.get((provider, section), ())
        identity = _IDENTITY_HEADERS.get((provider, section), 'Observation')
        headings = (identity, *map(_label, fields), 'Evidence', 'Source and scope', 'Capture time')
        table = _result_table(caption, headings, (_row_cells(path, row, fields) for row in visible))
        shown = {row.section.entry['id'] for row in visible}
        if item and visible and source and provider == 'incus':
            links = _placement_links(visible[0].section.entry, item, _related(enabled))
            evidence = _detail_links(links, 'Configured placements')
        context = [_section_summary(value) for value in observed
                   if value.entry['id'] in shown or source
                   or (not (q or item) and (not status or value.status == status)
                       and (value.status != 'complete' or not value.complete or not value.complete['records']))]
        if context:
            evidence += '<section class="stack"><h2>Source and section coverage</h2>' + ''.join(context) + '</section>'
    empty = '' if visible else _empty_inventory(provider, sources, source, section_key, sections, bool(q or item or status))
    description = f'<p>{_e(description)}</p>' if description else ''
    return (_section_links(path, provider, sections, section_key, params)
            + _filters(path, all_sources, sections, statuses, params)
            + description + count + empty + table + pagination + evidence)
