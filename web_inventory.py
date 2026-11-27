"""Serve a private, scope-filtered inventory on IPv4 loopback only."""

import argparse
from base64 import b64encode
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
from math import ceil
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
_BAD_PERCENT = re.compile(r'%(?![0-9a-fA-F]{2})')
_DETAIL_PATHS = {'/machines/detail', '/incus/detail', '/unifi/detail'}
_DETAIL_FIELDS = {'source', 'item'}
_COOKIE = 'silentdeluge_session'
_SESSION_SECONDS = 8 * 60 * 60
_SESSIONS_PER_USER = 8
_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW = 30
_PREVIEW_SECONDS = 300
_DNS_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
_ITERATIONS = 600_000
_STATE_NAME = 'inventory.sqlite3'
_LISTEN = '127.0.0.1'
# The design shell inlines exactly styles(); allow that block without allowing arbitrary CSS.
_STYLE_HASH = b64encode(sha256(styles().encode('utf-8')).digest()).decode('ascii')
_CSP = (f"default-src 'none'; style-src 'self' 'sha256-{_STYLE_HASH}'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


@dataclass(frozen=True)
class _User:
    salt: bytes
    verifier: bytes
    view: frozenset
    collect: frozenset


@dataclass
class _Session:
    username: str
    csrf: str
    expires: float
    preview: tuple[str, str, float] | None = None


@dataclass(frozen=True)
class _Source:
    id: str
    provider: str
    scope: str
    read: str
    config: dict
    declarations: dict | None
    import_only: bool
    network_domain: str | None
    placement: dict | None

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
        users[name] = _User(bytes.fromhex(digest[1]), bytes.fromhex(digest[2]), view, collect)
    if not isinstance(config['sources'], dict):
        raise ValueError('sources must be an object')
    sources = {}
    for name, source in config['sources'].items():
        _shape(source, {'provider', 'scope', 'read', 'config'},
               {'declarations', 'import_only', 'network_domain', 'placement'})
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
        network_domain = source.get('network_domain')
        if 'network_domain' in source and (not _text(network_domain) or len(network_domain) > 128):
            raise ValueError('invalid network domain')
        placement = source.get('placement')
        if 'placement' in source:
            _shape(placement, {'scope', 'name'})
            if not all(map(_text, placement.values())) or len(placement['name']) > 256:
                raise ValueError('invalid placement')
        sources[name] = _Source(name, provider, scope, read, dict(settings, scope=scope),
                                declarations, import_only, network_domain, placement)
    incus_scopes = {source.scope for source in sources.values() if source.provider == 'incus'}
    if any(source.placement is not None and source.placement['scope'] not in incus_scopes
           for source in sources.values()):
        raise ValueError('placement must reference a configured Incus scope')
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


def _document(title, content):
    return page(title, f'<div class="patterns stack"><header class="report-header">'
                       f'<h1>{_e(title)}</h1></header>{content}</div>')


def _hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{_e(value)}">'


def _login_page(error=''):
    message = f'<p role="alert">{_e(error)}</p>' if error else ''
    return _document('Sign in', message + '''
<form method="post" action="/login" class="stack">
  <label>Username <input name="username" autocomplete="username" required></label>
  <label>Password <input type="password" name="password" autocomplete="current-password" required></label>
  <button type="submit">Sign in</button>
</form>''')


def _may_collect(source, user):
    return not source.import_only and source.scope in user.collect


def _workspace_entries(db, username, user, sources):
    entries = []
    for source in sources.values():
        if source.scope not in user.view:
            continue
        enabled = _enabled(db, username, source)
        placement = source.placement
        if placement is not None and placement['scope'] not in user.view:
            placement = None
        entries.append(dict(id=source.id, provider=source.provider, scope=source.scope,
                            read=source.read, enabled=enabled, can_collect=_may_collect(source, user),
                            import_only=source.import_only, state=_state(db, source) if enabled else None,
                            declarations=source.declarations if enabled else None, asset_links=[],
                            network_domain=source.network_domain, placement=placement))
    return entries


def _preview(source, csrf, token):
    fields = _hidden('csrf', csrf) + _hidden('source', source.id) + _hidden('preview', token)
    return (f'<p>Source: {_e(source.id)} | Provider: {_e(source.provider)} | Scope: {_e(source.scope)}</p>'
            f'<p>Configured read: {_e(source.read)}</p>'
            '<p>Permission: one read of this configured source is granted. No probe or schedule is created.</p>'
            f'<p>Evidence destination: private local <code>{_STATE_NAME}</code> store, visible only to '
            'accounts granted this scope with the evidence view enabled.</p>'
            '<p>Nothing has been collected. This preview expires in five minutes.</p>'
            f'<form method="post" action="/collect">{fields}<button type="submit">Collect once</button></form>'
            '<p><a href="/collectors">Return to collectors</a></p>')


def _matches(expected, provided):
    return bool(_TOKEN.fullmatch(provided) and compare_digest(expected, provided))


def _parameters(encoded, *, max_fields):
    if _BAD_PERCENT.search(encoded):
        raise ValueError('invalid percent escape')
    fields = parse_qs(encoded, keep_blank_values=True, strict_parsing=True,
                      errors='strict', max_num_fields=max_fields)
    if any(len(values) != 1 for values in fields.values()):
        raise ValueError('duplicate parameter')
    return {key: value[0] for key, value in fields.items()}


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

    def _check_host(self):
        if self._header('Host') == self.server.authority:
            return True
        self._message(400, 'Invalid Host')
        return False

    def _session_token(self):
        token = None
        for cookie in (self._header('Cookie') or '').split(';'):
            key, _, value = cookie.strip().partition('=')
            if key == _COOKIE:
                if token is not None or not _TOKEN.fullmatch(value):
                    return None
                token = value
        return token

    def _session(self, token):
        session = self.server.sessions.get(token)
        if session is not None and session.expires <= monotonic():
            del self.server.sessions[token]
            return None
        return session

    def _cookie(self, token, age):
        secure = '; Secure' if self.server.secure_cookie else ''
        return f'{_COOKIE}={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}'

    def _login(self, token, form):
        try:
            if form is None or form.keys() != {'username', 'password'}:
                raise ValueError('invalid form fields')
            username = form['username']
            if not _ID.fullmatch(username):
                raise ValueError('invalid username')
            password = _password_bytes(form['password'])
        except ValueError:
            self._send(400, _login_page('Enter a valid username and password.'))
            return

        user = self.server.users.get(username)
        # One bucket per configured account and one for all unknown names. A
        # rejected retry does not move the deadline or affect active sessions.
        bucket = username if user is not None else None
        attempts = self.server.login_failures
        now = monotonic()
        count, deadline = attempts.get(bucket, (0, 0))
        if deadline <= now:
            count, deadline = 0, now + _LOGIN_WINDOW
        if count >= _LOGIN_ATTEMPTS:
            self._send(429, _login_page('Too many attempts. Try again shortly.'),
                       Retry_After=str(ceil(deadline - now)))
            return

        verifier = user if user is not None else self.server.unknown_user
        if not compare_digest(_derive(password, verifier.salt), verifier.verifier) or user is None:
            attempts[bucket] = (count + 1, deadline)
            self._send(401, _login_page('Invalid username or password.'))
            return

        attempts.pop(bucket, None)
        sessions = self.server.sessions
        sessions.pop(token, None)  # A successful sign-in always rotates any presented session.
        now = monotonic()
        for expired in [key for key, session in sessions.items() if session.expires <= now]:
            del sessions[expired]
        active = [key for key, session in sessions.items() if session.username == username]
        if len(active) >= _SESSIONS_PER_USER:
            del sessions[active[0]]  # Insertion order; one account cannot evict another.
        token = token_urlsafe(32)
        sessions[token] = _Session(username, token_urlsafe(32), now + _SESSION_SECONDS)
        self._send(303, Location='/', Set_Cookie=self._cookie(token, _SESSION_SECONDS))

    def _read_form(self):
        if self._header('Content-Type') != 'application/x-www-form-urlencoded' or 'Transfer-Encoding' in self.headers:
            return None
        length = self._header('Content-Length') or ''
        if not re.fullmatch(r'[0-9]{1,4}', length) or not 1 <= int(length) <= 4096:
            return None
        try:
            data = self.rfile.read(int(length))
            if len(data) == int(length):
                return _parameters(data.decode('ascii'), max_fields=3)
        except (ValueError, TimeoutError):
            pass
        return None

    def do_GET(self):
        if not self._check_host():
            return
        path, _, raw_query = self.path.partition('?')
        try:
            if (not path.startswith('/') or '#' in self.path or not self.path.isascii()
                    or not self.path.isprintable() or len(raw_query) > 2048):
                raise ValueError('invalid request target')
            query = _parameters(raw_query, max_fields=8)
            if any(not _ID.fullmatch(key) or len(value) > 256 or (value and not value.isprintable())
                   for key, value in query.items()):
                raise ValueError('invalid query')
            if path in _DETAIL_PATHS and (not query.keys() <= _DETAIL_FIELDS
                                         or ('source' in query and not _ID.fullmatch(query['source']))):
                raise ValueError('invalid detail query')
        except ValueError:
            self._message(400, 'Invalid request target')
            return
        if path == '/style.css':
            self._send(200, styles(), 'text/css; charset=utf-8')
            return
        session = self._session(self._session_token())
        if path == '/login':
            if session is None:
                self._send(200, _login_page())
            else:
                self._send(303, Location='/')
            return
        if session is None:
            self._send(303, Location='/login')
            return
        username = session.username
        if path == '/patterns':
            self._send(200, patterns(username, session.csrf))
            return
        try:
            with closing(sqlite3.connect(self.server.db)) as db:
                entries = _workspace_entries(db, username, self.server.users[username], self.server.sources)
        except sqlite3.Error:
            self._message(500, 'Local evidence store unavailable')
            return
        html = self.server.render_workspace(path, query, entries, username, session.csrf)
        if html is None:
            self._message(404, 'Not found')
        else:
            self._send(200, html)

    def do_POST(self):
        if not self._check_host():
            return
        if self._header('Origin') != self.server.origin:
            self._message(403, 'Origin rejected')
            return
        token = self._session_token()
        if self.path == '/login':
            self._login(token, self._read_form())
            return
        session = self._session(token)
        if session is None:
            self._message(401, 'Sign-in required')
            return
        if self.path not in ('/logout', '/enable', '/disable', '/preview', '/collect'):
            self._message(404, 'Not found')
            return
        form = self._read_form()
        fields = {'csrf'}
        if self.path != '/logout':
            fields.add('source')
        if self.path == '/collect':
            fields.add('preview')
        if form is None or form.keys() != fields:
            self._message(400, 'Invalid form')
            return
        if not _matches(session.csrf, form['csrf']):
            self._message(403, 'CSRF token rejected')
            return
        if self.path == '/logout':
            del self.server.sessions[token]
            self._send(303, Location='/login', Set_Cookie=self._cookie('', 0))
            return
        user = self.server.users[session.username]
        source = self.server.sources.get(form['source'])
        if source is None or source.scope not in user.view:
            self._message(404, 'Not found')
            return
        try:
            with closing(sqlite3.connect(self.server.db)) as db:
                self._action(db, session, user, source, form)
        except sqlite3.Error:
            self._message(500, 'Local evidence store unavailable')

    def _action(self, db, session, user, source, form):
        username = session.username
        if self.path in ('/enable', '/disable'):
            with db:
                if self.path == '/enable':
                    db.execute('INSERT OR IGNORE INTO enabled VALUES (?, ?)', (username, source.key))
                else:
                    db.execute('DELETE FROM enabled WHERE username=? AND source=?', (username, source.key))
            for active in self.server.sessions.values():
                if active.username == username:
                    active.preview = None
        else:
            if not _may_collect(source, user) or not _enabled(db, username, source):
                self._message(403, 'Collection not permitted')
                return
            if self.path == '/preview':
                from web_design import dashboard_page

                csrf = session.csrf
                token = token_urlsafe(32)
                session.preview = (source.key, token, monotonic() + _PREVIEW_SECONDS)
                self._send(200, dashboard_page('/collectors', 'Preview collection',
                                               _preview(source, csrf, token), username, csrf))
                return
            pending = session.preview
            if (pending is None or pending[0] != source.key or pending[2] <= monotonic()
                    or not _matches(pending[1], form['preview'])):
                self._message(403, 'A fresh collection preview is required')
                return
            session.preview = None
            _collect(db, source)
        self._send(303, Location=f'/collectors?source={source.id}')


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
    # Offline commands do not need the workspace renderer.
    from workspace_pages import render

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
    server.secure_cookie = public_authority is not None
    server.unknown_user = _User(token_bytes(16), token_bytes(32), frozenset(), frozenset())
    server.sessions = {}
    server.login_failures = {}
    server.render_workspace = render
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
