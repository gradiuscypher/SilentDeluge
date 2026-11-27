"""Serve a private, scope-filtered inventory on IPv4 loopback only."""

import argparse
from base64 import b64decode, b64encode
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from getpass import getpass
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
import json
import os
from pathlib import Path
import re
from secrets import token_bytes, token_urlsafe
import sqlite3
import stat
from time import monotonic
from urllib.parse import parse_qs

from collection import _PROVIDERS, _validate_sections
from inventory import compare
from web_design import page, patterns, styles


_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}')
_VERIFIER = re.compile(r'pbkdf2-sha256\$600000\$([0-9a-f]{32})\$([0-9a-f]{64})')
_TOKEN = re.compile(r'[a-zA-Z0-9_-]{43}')
_DNS_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
_ITERATIONS = 600_000
_STATE_NAME = 'inventory.sqlite3'
_LISTEN = '127.0.0.1'
# The design shell inlines exactly styles(); allow that block without allowing arbitrary CSS.
_STYLE_HASH = b64encode(sha256(styles().encode('utf-8')).digest()).decode('ascii')
_CSP = (f"default-src 'none'; style-src 'self' 'sha256-{_STYLE_HASH}'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

_SECTION_LABELS = {
    ('incus', 'instances'): ('Incus instances', 'Instance state, effective limits, and provider context.'),
    ('incus', 'runtime'): ('Runtime sightings', 'Runtime sightings do not cover the instance inventory.'),
    ('linux', 'packages'): ('Installed packages', 'Packages recorded by the guest’s selected package manager.'),
    ('linux', 'cpu'): ('CPU usage', 'Sampled CPU usage in the reported accounting view, not CPU capacity.'),
    ('linux', 'memory'): ('Memory', 'Usage and capacity in the reported accounting view. '
                          'A null limit means uncapped at that cgroup.'),
    ('linux', 'disk'): ('Root filesystem', 'Space usage for the root filesystem, not all guest storage.'),
    ('linux', 'users'): ('Local accounts', 'Local user names, UIDs, and primary GIDs.'),
    ('linux', 'interfaces'): ('Network interfaces', 'Names and indices in the observed network namespace.'),
}
_FIELD_LABELS = {
    'incus': {'cpu': 'CPU limit (count or pin set)', 'memory': 'Memory limit (as reported)'},
    'linux': {
        'sample_seconds': 'Sample duration (s)',
        'busy_percent': 'Busy CPU time (%)',
        'cpu_cores_used': 'Average CPU usage (cores)',
        'total_bytes': 'Total (bytes)',
        'used_bytes': 'Used (bytes)',
        'available_bytes': 'Available (bytes)',
        'limit_bytes': 'Limit (bytes)',
    },
}


@dataclass(frozen=True)
class _User:
    salt: bytes
    verifier: bytes
    view: frozenset
    collect: frozenset
    csrf: str


@dataclass(frozen=True)
class _Source:
    id: str
    provider: str
    scope: str
    read: str
    config: dict
    declarations: dict | None
    import_only: bool

    @property
    def key(self):
        # Never reuse persisted state if a source ID is assigned to another scope/provider.
        return json.dumps([self.id, self.provider, self.scope])


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate JSON key')
    return result


def _shape(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= value.keys() <= required | optional:
        raise ValueError('unexpected configuration fields')


def _text(value):
    return isinstance(value, str) and bool(value) and value == value.strip() and value.isprintable()


def _scopes(values):
    if not isinstance(values, list) or not all(map(_text, values)):
        raise ValueError('invalid scope list')
    return frozenset(values)


def _derive(password, salt):
    return pbkdf2_hmac('sha256', password, salt, _ITERATIONS)


def _password_bytes(password):
    value = password.encode('utf-8')
    if not 1 <= len(value) <= 1024:
        raise ValueError('password must be 1–1024 UTF-8 bytes')
    return value


def _declarations(document, scope):
    _shape(document, {'source', 'scope', 'timestamp', 'containers', 'vms'})
    if document['scope'] != scope or not _text(document['source']):
        raise ValueError('invalid declaration provenance')
    for group in ('containers', 'vms'):
        if not isinstance(document[group], list):
            raise ValueError('expected a list of declared guests')
        for guest in document[group]:
            _shape(guest, {'name'}, {'cpu', 'memory'})
            if not all(map(_text, guest.values())):
                raise ValueError('invalid guest declaration')
    compare(document, [])  # Check timestamp and duplicate names using the offline contract.
    return document


def _check_private(info, kind):
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('expected a private file or directory owned by the server user')


def _load_private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding='utf-8') as stream:
        _check_private(os.fstat(stream.fileno()), stat.S_ISREG)
        return json.load(stream, object_pairs_hook=_unique_object)


def _load_config(path):
    config = _load_private_json(path)
    _shape(config, {'state_dir', 'users', 'sources'})
    if not _text(config['state_dir']) or not Path(config['state_dir']).is_absolute():
        raise ValueError('state_dir must be an absolute path')
    if not isinstance(config['users'], dict) or not config['users']:
        raise ValueError('at least one user is required')
    users = {}
    for name, user in config['users'].items():
        _shape(user, {'password_hash', 'view_scopes', 'collect_scopes'})
        encoded = user['password_hash']
        digest = _VERIFIER.fullmatch(encoded) if isinstance(encoded, str) else None
        view, collect = _scopes(user['view_scopes']), _scopes(user['collect_scopes'])
        if not _ID.fullmatch(name) or not digest or not collect <= view:
            raise ValueError('invalid credentials or grants')
        users[name] = _User(bytes.fromhex(digest[1]), bytes.fromhex(digest[2]), view, collect,
                            token_urlsafe(32))
    if not isinstance(config['sources'], dict):
        raise ValueError('sources must be an object')
    sources = {}
    for name, source in config['sources'].items():
        _shape(source, {'provider', 'scope', 'read', 'config'}, {'declarations', 'import_only'})
        provider, scope, read, settings = (source[key] for key in ('provider', 'scope', 'read', 'config'))
        import_only = source.get('import_only', False)
        if (not _ID.fullmatch(name) or not isinstance(provider, str) or provider not in _PROVIDERS
                or not _text(scope) or not _text(read) or not isinstance(settings, dict)
                or 'scope' in settings or type(import_only) is not bool or (import_only and settings)):
            raise ValueError('invalid allowlisted source')
        declarations = None
        if 'declarations' in source:
            if provider != 'incus':
                raise ValueError('declarations are only supported for Incus instances')
            declarations = _declarations(source['declarations'], scope)
        sources[name] = _Source(name, provider, scope, read, dict(settings, scope=scope),
                                declarations, import_only)
    return users, sources, Path(config['state_dir'])


def _open_store(state_dir):
    _check_private(state_dir.stat(follow_symlinks=False), stat.S_ISDIR)
    path = state_dir / _STATE_NAME
    fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _check_private(os.fstat(fd), stat.S_ISREG)
    finally:
        os.close(fd)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS enabled (
                username TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (username, source)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                source TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL
            );
        ''')
    return path


def _enabled(db, username, source):
    return db.execute('SELECT 1 FROM enabled WHERE username=? AND source=?',
                      (username, source.key)).fetchone() is not None


def _state(db, source):
    result = db.execute('SELECT state FROM evidence WHERE source=?', (source.key,)).fetchone()
    return json.loads(result[0]) if result else None


def _instant(value):
    return datetime.fromisoformat(value)


def _timestamp():
    return datetime.now(timezone.utc).isoformat().removesuffix('+00:00') + 'Z'


def _validate_captures(provider, sections):
    _validate_sections(sections)
    names = set()
    for section in sections:
        name, outcome, records = (section[key] for key in ('section', 'outcome', 'records'))
        if not _text(name) or name in names or (outcome == 'failed' and records):
            raise ValueError('invalid observation section')
        names.add(name)
        if provider == 'incus' and name == 'instances' and outcome == 'complete':
            # Only this population feeds declaration comparison; other fields remain source-local.
            guests = set()
            for row in records:
                guest = row.get('name')
                if (not _text(guest) or guest in guests or not _text(row.get('state'))
                        or row.get('kind') not in ('container', 'virtual-machine')
                        or any(row.get(key) is not None and not isinstance(row[key], str)
                               for key in ('cpu', 'memory'))):
                    raise ValueError('invalid complete Incus instance')
                guests.add(guest)
    json.dumps(sections, allow_nan=False)


def _save_evidence(db, source, timestamp, sections, *, imported=False, outcome=None):
    # The HTTP server and operator CLI can both write; lock before reading history.
    with db:
        db.execute('BEGIN IMMEDIATE')
        state = _state(db, source) or {'sections': {}}
        results = {}
        for section in sections:
            name = section['section']
            capture = section.get('timestamp', timestamp)
            history = state['sections'].setdefault(name, {})
            if 'latest' in history and _instant(capture) <= _instant(history['latest']):
                results[name] = {'outcome': 'no newer evidence', 'timestamp': capture}
                continue
            result = section['outcome']
            results[name] = {'outcome': result, 'timestamp': capture}
            history['latest'] = capture
            if result == 'complete':
                history['complete'] = {'timestamp': capture, 'records': section['records']}
                history.pop('partial', None)
            elif result == 'partial' and section['records']:
                history['partial'] = {'timestamp': capture, 'records': section['records']}
        if outcome is None:
            outcome = 'Results received' if sections else 'No sections returned'
        state.update(timestamp=timestamp, outcome=outcome, results=results, imported=imported)
        db.execute('INSERT INTO evidence VALUES (?, ?) ON CONFLICT(source) DO UPDATE SET state=excluded.state',
                   (source.key, json.dumps(state, allow_nan=False)))


def _collect(db, source):
    outcome = None
    try:
        sections = import_module(_PROVIDERS[source.provider]).collect(source.config)
        timestamp = _timestamp()
        _validate_captures(source.provider, sections)
    except (Exception, SystemExit):
        # Provider diagnostics can contain paths, credentials or response bodies.
        timestamp, sections, outcome = _timestamp(), [], 'Collection failed'
    _save_evidence(db, source, timestamp, sections, outcome=outcome)


def _import_evidence(config_path, source_id, report_path):
    _, sources, state_dir = _load_config(config_path)
    source = sources.get(source_id)
    if source is None:
        raise ValueError('source ID not configured')
    envelope = _load_private_json(report_path)
    _shape(envelope, {'schema_version', 'source', 'scope', 'timestamp', 'sections'})
    version, timestamp = envelope['schema_version'], envelope['timestamp']
    if (type(version) is not int or version != 1 or envelope['source'] != source.provider
            or envelope['scope'] != source.scope or not _text(timestamp)
            or _instant(timestamp).utcoffset() is None):
        raise ValueError('invalid evidence provenance')
    sections = envelope['sections']
    _validate_captures(source.provider, sections)
    with closing(sqlite3.connect(_open_store(state_dir))) as db:
        _save_evidence(db, source, timestamp, sections, imported=True)


def _e(value):
    return escape(str(value), quote=True)


def _cell(value):
    return _e(value if isinstance(value, str) and value else json.dumps(value, ensure_ascii=False))


def _table(caption, headers, rows):
    heading = ''.join(f'<th scope="col">{_e(header)}</th>' for header in headers)
    return (f'<table class="data-table"><caption>{_e(caption)}</caption><thead><tr>{heading}</tr></thead>'
            '<tbody>' + ''.join(
                '<tr>' + ''.join(f'<td>{_cell(value)}</td>' for value in row) + '</tr>' for row in rows
            ) + '</tbody></table>')


def _capture(source, name, label, capture, now):
    records = capture['records']
    count = len(records)
    rows_label = f'{count} {"row" if count == 1 else "rows"}'
    citation = _citation(source.id, source.scope, capture['timestamp'], now)
    summary = f'<p>{label}: {citation} — {rows_label}.</p>'
    if not records:
        return summary + '<p>No records in this capture.</p>'
    columns = dict.fromkeys(key for row in records for key in row)
    if not columns:
        return summary + '<p>Records have no fields.</p>'
    field_labels = _FIELD_LABELS.get(source.provider, {})
    table = _table(label, (field_labels.get(key, key) for key in columns),
                   ([row.get(key, '—') for key in columns] for row in records))
    if (source.provider, name) == ('linux', 'packages'):
        table = (f'<details class="disclosure"><summary>Show {label.lower()} packages ({rows_label})</summary>'
                 f'{table}</details>')
    return summary + table


def _date(timestamp, now):
    age = (now - _instant(timestamp)).total_seconds()
    age_text = f'age {int(age)} s' if age >= 0 else 'timestamp ahead of server clock'
    return f'<time datetime="{_e(timestamp)}">{_e(timestamp)}</time> ({age_text})'


def _citation(source, scope, timestamp, now):
    return f'{_e(source)} | {_e(scope)} | {_date(timestamp, now)}'


def _comparison(source, complete, outcome, now):
    scans = []
    if complete:
        # The Incus provider emits normalized observed fields; compare consumes the
        # original offline scan shape with effective limits in expanded_config.
        instances = [dict(name=row['name'], type=row['kind'], status=row['state'],
                          expanded_config={'limits.cpu': row['cpu'], 'limits.memory': row['memory']})
                     for row in complete['records']]
        scans = [dict(source=source.id, scope=source.scope, timestamp=complete['timestamp'],
                      outcome='complete', instances=instances)]
    rows = []
    for row in compare(source.declarations, scans):
        status = row['status']
        if outcome not in (None, 'complete'):
            status = 'stale' if row['observed'] is not None else 'unknown'
        rows.append((row['name'], status, row['declared'] or '—', row['observed'] or '—',
                     ', '.join(row['differences']) or '—'))
    declaration = source.declarations
    html = '<p>Declarations: ' + _citation(declaration['source'], declaration['scope'],
                                           declaration['timestamp'], now) + '</p>'
    if rows:
        html += _table('Declaration comparison', ('Instance', 'Status', 'Declared', 'Observed', 'Differing fields'),
                       rows)
    else:
        html += '<p>No declared or observed instances.</p>'
    return html


def _inventory(source, state, now):
    parts = []
    if source.declarations is None:
        parts.append('<p>No declarations configured.</p>')
    if state is None:
        parts.append('<p>Not connected: no offline evidence imported.</p>' if source.import_only
                     else '<p>Not connected: no collection attempted.</p>')
        if source.declarations is not None:
            parts.append(_comparison(source, None, None, now))
        return ''.join(parts)

    label = 'Latest processed offline envelope' if state.get('imported') else 'Latest collection'
    parts.append(f'<p>{label}: {_e(state["outcome"])} — {_date(state["timestamp"], now)}.</p>')
    names = state['sections'].keys() | ({'instances'} if source.declarations is not None else set())
    if not names:
        parts.append('<p>No observations available. Presence is unknown.</p>')
    for name in sorted(names):
        title, description = _SECTION_LABELS.get((source.provider, name), (name, ''))
        parts.append(f'<section class="stack"><h3>{_e(title)}</h3>')
        if description:
            parts.append(f'<p>{_e(description)}</p>')
        result = state['results'].get(name)
        outcome = result['outcome'] if result else 'not returned'
        parts.append(f'<p>Latest section outcome: {_e(outcome)}' +
                     (f' — {_date(result["timestamp"], now)}' if result else '') + '.</p>')
        if outcome != 'complete':
            parts.append('<p>Presence of omitted records is unknown.</p>')
        history = state['sections'].get(name, {})
        complete, partial = history.get('complete'), history.get('partial')
        if source.declarations is not None and name == 'instances':
            parts.append(_comparison(source, complete, outcome, now))
        if complete:
            label = 'Complete capture' if outcome == 'complete' else 'Stale — last verified complete capture'
            parts.append(_capture(source, name, label, complete, now))
        current_partial = partial and outcome == 'partial' and result['timestamp'] == partial['timestamp']
        if outcome == 'partial' and not current_partial:
            parts.append('<p>No sightings in this partial capture (0 rows).</p>')
        if partial:
            label = 'Partial sightings' if current_partial else 'Stale — retained partial sightings'
            parts.append(_capture(source, name, label, partial, now))
        parts.append('</section>')
    return ''.join(parts)


def _document(title, content):
    return page(title, f'<div class="patterns stack"><header class="report-header">'
                       f'<h1>{_e(title)}</h1></header>{content}</div>')


def _hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{_e(value)}">'


def _form(action, source, user, label, preview=None):
    fields = _hidden('csrf', user.csrf) + _hidden('source', source.id)
    if preview is not None:
        fields += _hidden('preview', preview)
    return f'<form method="post" action="/{action}">{fields}<button type="submit">{label}</button></form>'


def _may_collect(source, user):
    return not source.import_only and source.scope in user.collect


def _dashboard(db, username, user, sources):
    visible = [source for source in sources.values() if source.scope in user.view]
    body = [f'<p>Signed in as <strong>{_e(username)}</strong>.</p>']
    if not visible:
        body.append('<p>No sources are available in your scopes.</p>')
    now = datetime.now(timezone.utc)
    for source in visible:
        enabled = _enabled(db, username, source)
        allowed = _may_collect(source, user)
        permission = ('disabled (import-only)' if source.import_only else
                      'granted' if allowed else 'not granted')
        body.append(f'<section class="panel" id="{_e(source.id)}"><h2>{_e(source.id)}</h2>'
                    f'<p>Provider: {_e(source.provider)} | Scope: {_e(source.scope)}</p>'
                    f'<p>Evidence view: {"enabled" if enabled else "disabled"}. '
                    f'Collection permission: {permission}.</p>')
        action = 'disable' if enabled else 'enable'
        body.append(_form(action, source, user, f'{action.capitalize()} evidence view'))
        if enabled:
            if allowed:
                body.append(_form('preview', source, user, 'Preview one collection'))
            body.append(_inventory(source, _state(db, source), now))
        body.append('</section>')
    return ''.join(body)


def _preview(source, user, token):
    return (f'<p>Source: {_e(source.id)} | Provider: {_e(source.provider)} | Scope: {_e(source.scope)}</p>'
            f'<p>Configured read: {_e(source.read)}</p>'
            '<p>Permission: one read of this configured source is granted. No probe or schedule is created.</p>'
            f'<p>Evidence destination: private local <code>{_STATE_NAME}</code> store, visible only to '
            'accounts granted this scope with the evidence view enabled.</p>'
            '<p>Nothing has been collected. This preview expires in five minutes.</p>'
            + _form('collect', source, user, 'Collect once', token)
            + '<p><a href="/">Return to inventory</a></p>')


def _matches(expected, provided):
    return bool(_TOKEN.fullmatch(provided) and compare_digest(expected, provided))


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass  # Never log request input, authorization, provider diagnostics or evidence.

    def _header(self, name):
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def _send(self, status, body='', content_type='text/html; charset=utf-8', **headers):
        data = body.encode('utf-8', errors='backslashreplace')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', _CSP)
        for name, value in headers.items():
            self.send_header(name.replace('_', '-'), value)
        self.end_headers()
        self.wfile.write(data)

    def _message(self, status, title):
        self._send(status, _document(title, '<p><a href="/">Inventory</a></p>'))

    def _authenticate(self):
        if self._header('Host') != self.server.authority:
            self._message(400, 'Invalid Host')
            return None
        auth = self._header('Authorization') or ''
        scheme, _, encoded = auth.partition(' ')
        if scheme.lower() == 'basic' and 0 < len(encoded) <= 1456:
            try:
                name, separator, password = b64decode(encoded, validate=True).decode('utf-8').partition(':')
                if separator:
                    user = self.server.users.get(name, self.server.unknown_user)
                    digest = _derive(_password_bytes(password), user.salt)
                    if compare_digest(digest, user.verifier) and name in self.server.users:
                        return name, user
            except ValueError:
                pass
        self._send(401, _document('Authentication required', '<p>Sign in with a configured local account.</p>'),
                   WWW_Authenticate='Basic realm="SilentDeluge", charset="UTF-8"')
        return None

    def _read_form(self):
        if self._header('Content-Type') != 'application/x-www-form-urlencoded' or 'Transfer-Encoding' in self.headers:
            return None
        length = self._header('Content-Length') or ''
        if not re.fullmatch(r'[0-9]{1,4}', length) or not 1 <= int(length) <= 2048:
            return None
        try:
            data = self.rfile.read(int(length))
            if len(data) != int(length):
                return None
            fields = parse_qs(data.decode('ascii'), keep_blank_values=True, strict_parsing=True,
                              errors='strict', max_num_fields=3)
        except (ValueError, TimeoutError):
            return None
        if all(len(values) == 1 for values in fields.values()):
            return {key: value[0] for key, value in fields.items()}
        return None

    def do_GET(self):
        login = self._authenticate()
        if login is None:
            return
        if self.path == '/style.css':
            self._send(200, styles(), 'text/css; charset=utf-8')
        elif self.path == '/patterns':
            self._send(200, patterns())
        elif self.path == '/':
            try:
                with closing(sqlite3.connect(self.server.db)) as db:
                    html = _dashboard(db, *login, self.server.sources)
            except sqlite3.Error:
                self._message(500, 'Local evidence store unavailable')
            else:
                self._send(200, _document('Inventory', html))
        else:
            self._message(404, 'Not found')

    def do_POST(self):
        login = self._authenticate()
        if login is None:
            return
        if self._header('Origin') != self.server.origin:
            self._message(403, 'Origin rejected')
            return
        if self.path not in ('/enable', '/disable', '/preview', '/collect'):
            self._message(404, 'Not found')
            return
        username, user = login
        form = self._read_form()
        expected = {'csrf', 'source', 'preview'} if self.path == '/collect' else {'csrf', 'source'}
        if form is None or form.keys() != expected:
            self._message(400, 'Invalid form')
            return
        if not _matches(user.csrf, form['csrf']):
            self._message(403, 'CSRF token rejected')
            return
        source = self.server.sources.get(form['source'])
        if source is None or source.scope not in user.view:
            self._message(404, 'Not found')
            return
        try:
            with closing(sqlite3.connect(self.server.db)) as db:
                self._action(db, username, user, source, form)
        except sqlite3.Error:
            self._message(500, 'Local evidence store unavailable')

    def _action(self, db, username, user, source, form):
        previews = self.server.previews
        if self.path in ('/enable', '/disable'):
            with db:
                if self.path == '/enable':
                    db.execute('INSERT OR IGNORE INTO enabled VALUES (?, ?)', (username, source.key))
                else:
                    db.execute('DELETE FROM enabled WHERE username=? AND source=?', (username, source.key))
            previews.pop(username, None)
        else:
            if not _may_collect(source, user) or not _enabled(db, username, source):
                self._message(403, 'Collection not permitted')
                return
            if self.path == '/preview':
                token = token_urlsafe(32)
                previews[username] = (source.key, token, monotonic() + 300)
                self._send(200, _document('Preview collection', _preview(source, user, token)))
                return
            pending = previews.get(username)
            if (pending is None or pending[0] != source.key or pending[2] <= monotonic()
                    or not _matches(pending[1], form['preview'])):
                self._message(403, 'A fresh collection preview is required')
                return
            del previews[username]
            _collect(db, source)
        self._send(303, Location=f'/#{source.id}')


class _InvalidPublicOrigin(ValueError):
    pass


def _public_authority(origin):
    if isinstance(origin, str) and origin.startswith('https://'):
        authority = origin.removeprefix('https://')
        if 0 < len(authority) <= 253:
            labels = authority.split('.')
            # Browsers interpret numeric final labels as IPv4, including legacy hex forms.
            if (not re.fullmatch(r'[0-9]+|0[xX][0-9A-Fa-f]*', labels[-1])
                    and all(_DNS_LABEL.fullmatch(label) for label in labels)):
                return authority
    raise _InvalidPublicOrigin('--public-origin requires https://hostname with no port or other URL components')


def make_server(config_path, port=8765, *, public_origin=None):
    """Construct a loopback server; port 0 lets the OS choose, even behind a TLS proxy."""
    public_authority = None if public_origin is None else _public_authority(public_origin)
    users, sources, state_dir = _load_config(config_path)
    db = _open_store(state_dir)
    # Serve serially: a preview is consumed and its evidence committed once, before another action.
    server = HTTPServer((_LISTEN, port), _Handler)
    if public_authority is None:
        actual_port = server.server_port
        server.authority = _LISTEN if actual_port == 80 else f'{_LISTEN}:{actual_port}'
        server.origin = f'http://{server.authority}'
    else:
        server.authority, server.origin = public_authority, public_origin
    server.users, server.sources, server.db = users, sources, db
    server.unknown_user = _User(token_bytes(16), token_bytes(32), frozenset(), frozenset(), '')
    server.previews = {}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('hash-password', help='prompt for a password and emit a salted verifier')
    ingest = commands.add_parser('import-evidence', help='import a private collection.py JSON envelope',
                                 allow_abbrev=False)
    ingest.add_argument('--config', type=Path, required=True)
    ingest.add_argument('--source', required=True, metavar='ID')
    ingest.add_argument('--file', type=Path, required=True)
    serve = commands.add_parser('serve', help='serve inventory on 127.0.0.1')
    serve.add_argument('--config', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--public-origin', metavar='https://hostname',
                       help='external HTTPS origin of a same-host TLS reverse proxy')
    args = parser.parse_args()
    if args.command == 'hash-password':
        try:
            password = _password_bytes(getpass('Password: '))
            if not compare_digest(password, _password_bytes(getpass('Repeat password: '))):
                raise ValueError('passwords did not match')
        except (ValueError, EOFError, KeyboardInterrupt):
            parser.exit(2, 'Could not read matching passwords.\n')
        salt = token_bytes(16)
        print(f'pbkdf2-sha256${_ITERATIONS}${salt.hex()}${_derive(password, salt).hex()}')
        return
    if args.command == 'import-evidence':
        try:
            _import_evidence(args.config, args.source, args.file)
        except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error):
            parser.exit(2, 'Could not import evidence: check private files, source ID and envelope contract.\n')
        print('Evidence envelope processed.')
        return
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    try:
        server = make_server(args.config, args.port, public_origin=args.public_origin)
    except _InvalidPublicOrigin as exc:
        serve.error(str(exc))
    except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error):
        parser.exit(2, 'Could not load private web configuration/state or bind the loopback port.\n')
    with server:
        print(server.origin, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
