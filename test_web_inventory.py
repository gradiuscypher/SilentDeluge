"""Synthetic CLI and HTTP acceptance for the local inventory; no live provider reads."""

from base64 import b64encode
from hashlib import pbkdf2_hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import re
from subprocess import run
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from web_inventory import make_server


_STAGE = 'theatre/stage'
_BOOTH = 'theatre/booth'
_SHARED = 'theatre/shared'
_PASSWORDS = {'alice': 'synthetic-alice-password', 'bob': 'synthetic-bob-password'}
_SCRIPT = Path(__file__).resolve().with_name('web_inventory.py')


def _envelope(provider, scope, minute, *sections):
    return dict(schema_version=1, source=provider, scope=scope,
                timestamp=f'2025-01-01T10:{minute:02d}:00Z', sections=list(sections))


def _instance(name, cpu='1'):
    return dict(name=name, type='container', status='Running', expanded_config={'limits.cpu': cpu})


class WebInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {}
        for n, (user, password) in enumerate(_PASSWORDS.items(), start=1):
            salt = bytes([n]) * 16
            digest = pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
            cls.hashes[user] = f'pbkdf2-sha256$600000${salt.hex()}${digest.hex()}'

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        state = self.root / 'state'
        state.mkdir(mode=0o700)
        self.path = self.root / 'web.json'
        self.config = {
            'state_dir': str(state),
            'users': {
                'alice': dict(password_hash=self.hashes['alice'], view_scopes=[_STAGE, _SHARED],
                              collect_scopes=[_STAGE, _SHARED]),
                'bob': dict(password_hash=self.hashes['bob'], view_scopes=[_BOOTH, _SHARED],
                            collect_scopes=[_BOOTH]),
            },
            'sources': {
                source: dict(provider='incus', scope=scope,
                             read='Read a saved <operator> snapshot.',
                             config=dict(snapshot_path=str(self.root / f'{source}.json')))
                for source, scope in [('stagelight', _STAGE), ('soundbooth', _BOOTH), ('shared', _SHARED)]
            },
        }

    def start(self, *, public_origin=None):
        self.cookies = {}
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        self.path.chmod(0o600)
        self.server = self.enterContext(make_server(self.path, port=0, public_origin=public_origin))
        thread = Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.local_origin = f'http://127.0.0.1:{self.server.server_port}'
        self.origin = public_origin or self.local_origin

    def capture(self, source, outcome, minute, *instances):
        path = Path(self.config['sources'][source]['config']['snapshot_path'])
        path.write_text(json.dumps(dict(scope=self.config['sources'][source]['scope'],
                                        timestamp=f'2025-01-01T10:{minute:02d}:00Z',
                                        outcome=outcome, instances=instances)), encoding='utf-8')

    def import_report(self, source, report, *, mode=0o600):
        path = self.root / 'approved-report.json'
        path.write_text(report if isinstance(report, str) else json.dumps(report), encoding='utf-8')
        path.chmod(mode)
        return run([sys.executable, str(_SCRIPT), 'import-evidence', '--config', str(self.path),
                    '--source', source, '--file', str(path)],
                   capture_output=True, text=True, timeout=5)

    def import_ok(self, source, report):
        result = self.import_report(source, report)
        self.assertEqual(result.returncode, 0, result.stderr)

    def raw_request(self, method, path, fields=None, headers=None, *, send_origin=True):
        request_headers = {'Host': self.origin.split('://', 1)[1]}
        if fields is not None:
            request_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if method == 'POST' and send_origin:
            request_headers['Origin'] = self.origin
        if headers:
            request_headers.update(headers)
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, None if fields is None else urlencode(fields), request_headers)
            response = conn.getresponse()
            return response.status, response.headers, response.read().decode()
        finally:
            conn.close()

    def sign_in(self, user, *, password=None, headers=None, send_origin=True):
        return self.raw_request('POST', '/login', {'username': user,
                                                   'password': _PASSWORDS[user] if password is None else password},
                                headers, send_origin=send_origin)

    def signed_in_cookie(self, user):
        status, headers, _ = self.sign_in(user)
        self.assertEqual((status, headers['Location']), (303, '/'))
        return headers['Set-Cookie'].split(';', 1)[0]

    def request(self, method, path, user='alice', fields=None, headers=None):
        request_headers = {}
        if user is not None:
            if user not in self.cookies:
                self.cookies[user] = self.signed_in_cookie(user)
            request_headers['Cookie'] = self.cookies[user]
        if headers:
            request_headers.update(headers)
        return self.raw_request(method, path, fields, request_headers)

    def workspace(self, path='/collectors', user='alice'):
        status, _, html = self.request('GET', path, user)
        self.assertEqual(status, 200, html)
        return html

    def incus(self, section='instances', *, source='stagelight', user='alice', **filters):
        query = urlencode(dict(source=source, section=section, **filters))
        return self.workspace(f'/incus?{query}', user)

    def field(self, html, key):
        match = re.search(fr'name="{key}" value="([^"]+)"', html)
        self.assertIsNotNone(match, f'Form field not found: {key}')
        return match[1]

    def post(self, action, source, csrf, user='alice', **fields):
        return self.request('POST', '/' + action, user, dict(source=source, csrf=csrf, **fields))

    def preview(self, source, csrf, user='alice'):
        status, _, html = self.post('preview', source, csrf, user)
        self.assertEqual(status, 200)
        return html

    def collect(self, source, csrf, user='alice'):
        token = self.field(self.preview(source, csrf, user), 'preview')
        status, headers, _ = self.post('collect', source, csrf, user, preview=token)
        self.assertEqual((status, headers['Location']), (303, '/collectors?source=' + source))
        return token

    def test_login_is_public_but_workspace_requires_a_session(self):
        self.config['sources'] = {}
        self.start()
        for path in ('/', '/machines', '/incus', '/unifi', '/relationships', '/collectors',
                     '/collectors/new', '/access', '/patterns'):
            with self.subTest(path=path):
                status, headers, body = self.raw_request('GET', path)
                self.assertEqual((status, headers['Location']), (303, '/login'))
                self.assertNotIn('WWW-Authenticate', headers)
                self.assertNotIn('Sources:', body)
                self.assertEqual(self.request('GET', path)[0], 200)
        credentials = b64encode(f'alice:{_PASSWORDS["alice"]}'.encode()).decode()
        self.assertEqual(self.raw_request('GET', '/', headers={'Authorization': f'Basic {credentials}'})[0], 303)
        status, _, html = self.raw_request('GET', '/login')
        self.assertEqual(status, 200)
        self.assertIn('action="/login"', html)
        self.assertIn('type="password"', html)
        self.assertEqual(self.raw_request('GET', '/login', headers={'Host': 'localhost'})[0], 400)
        self.assertEqual(self.raw_request('GET', '/style.css')[1].get_content_type(), 'text/css')
        self.assertEqual(self.request('GET', '/unknown')[0], 404)
        self.assertEqual(self.request('GET', '/logout')[0], 404)
        self.assertIn('action="/logout"', self.workspace('/'))

    def test_login_origin_logout_csrf_and_revocation(self):
        self.start()
        self.assertEqual(self.sign_in('alice', headers={'Host': 'localhost'})[0], 400)
        self.assertEqual(self.sign_in('alice', send_origin=False)[0], 403)
        self.assertEqual(self.sign_in('alice', headers={'Origin': 'null'})[0], 403)
        status, headers, wrong = self.sign_in('alice', password='wrong')
        self.assertEqual(status, 401)
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.sign_in('not-configured', password='wrong')[2], wrong)

        status, headers, _ = self.sign_in('alice')
        self.assertEqual(status, 303)
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Path=/', cookie)
        self.assertNotIn('Secure', cookie)
        first_cookie = {'Cookie': cookie.split(';', 1)[0]}
        second_cookie = {'Cookie': self.signed_in_cookie('alice')}
        first_csrf = self.field(self.raw_request('GET', '/', headers=first_cookie)[2], 'csrf')
        second_csrf = self.field(self.raw_request('GET', '/', headers=second_cookie)[2], 'csrf')
        self.assertNotEqual(first_cookie, second_cookie)
        self.assertNotEqual(first_csrf, second_csrf)

        form = {'csrf': first_csrf}
        self.assertEqual(self.raw_request('POST', '/logout', form, first_cookie, send_origin=False)[0], 403)
        self.assertEqual(self.raw_request('POST', '/logout', form, second_cookie)[0], 403)
        status, headers, _ = self.raw_request('POST', '/logout', form, first_cookie)
        self.assertEqual((status, headers['Location']), (303, '/login'))
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.raw_request('GET', '/', headers=first_cookie)[0], 303)
        self.assertEqual(self.raw_request('POST', '/enable', dict(form, source='stagelight'), first_cookie)[0], 401)
        self.assertEqual(self.raw_request('GET', '/', headers=second_cookie)[0], 200)

        status, headers, _ = self.sign_in('alice', headers=second_cookie)
        self.assertEqual(status, 303)
        self.assertNotEqual(headers['Set-Cookie'].split(';', 1)[0], second_cookie['Cookie'])
        self.assertEqual(self.raw_request('GET', '/', headers=second_cookie)[0], 303)

    def test_sessions_expire_and_only_evict_the_same_accounts_oldest_session(self):
        self.start()
        clock = Mock(return_value=100.0)
        with patch('web_inventory.monotonic', clock):
            bob = {'Cookie': self.signed_in_cookie('bob')}
            first = {'Cookie': self.signed_in_cookie('alice')}
            for _ in range(7):
                self.signed_in_cookie('alice')
            self.assertEqual(self.raw_request('GET', '/', headers=first)[0], 200)
            latest = {'Cookie': self.signed_in_cookie('alice')}
            self.assertEqual(self.raw_request('GET', '/', headers=first)[0], 303)
            self.assertEqual(self.raw_request('GET', '/', headers=bob)[0], 200)
            self.assertEqual(self.raw_request('GET', '/', headers=latest)[0], 200)
            clock.return_value += 8 * 60 * 60
            self.assertEqual(self.raw_request('GET', '/', headers=latest)[0], 303)

    def test_login_throttle_does_not_extend_window_or_lock_other_accounts(self):
        self.start()
        clock = Mock(return_value=100.0)
        with patch('web_inventory.monotonic', clock):
            existing = {'Cookie': self.signed_in_cookie('alice')}
            for _ in range(5):
                self.assertEqual(self.sign_in('alice', password='wrong')[0], 401)
            status, headers, _ = self.sign_in('alice', password='wrong')
            self.assertEqual((status, headers['Retry-After']), (429, '30'))
            self.assertEqual(self.sign_in('bob')[0], 303)
            self.assertEqual(self.raw_request('GET', '/', headers=existing)[0], 200)
            clock.return_value += 29
            status, headers, _ = self.sign_in('alice', password='wrong')
            self.assertEqual((status, headers['Retry-After']), (429, '1'))
            clock.return_value += 1
            self.assertEqual(self.sign_in('alice')[0], 303)

    def test_public_origin_requires_exact_host_and_post_origin(self):
        authority = 'inventory.example.test'
        origin = f'https://{authority}'
        self.start(public_origin=origin)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

        spoofed = {'Forwarded': f'host={authority};proto=https',
                   'X-Forwarded-Host': authority, 'X-Forwarded-Proto': 'https',
                   'X-Forwarded-Port': '443', 'X-Forwarded-Origin': origin}
        for host in (self.local_origin.removeprefix('http://'), authority.upper(), f'{authority}:443'):
            with self.subTest(host=host):
                self.assertEqual(self.raw_request('GET', '/', headers={**spoofed, 'Host': host})[0], 400)
                self.assertEqual(self.sign_in('alice', headers={**spoofed, 'Host': host})[0], 400)

        status, headers, _ = self.sign_in('alice')
        self.assertEqual(status, 303)
        self.assertIn('Secure', headers['Set-Cookie'])
        cookie = {'Cookie': headers['Set-Cookie'].split(';', 1)[0]}
        status, _, html = self.raw_request('GET', '/', headers=cookie)
        self.assertEqual(status, 200)
        form = dict(csrf=self.field(html, 'csrf'), source='stagelight')
        for wrong_origin in (None, 'null', self.local_origin, f'http://{authority}',
                             f'https://{authority.upper()}', f'{origin}:443', f'{origin}/'):
            with self.subTest(origin=wrong_origin):
                headers = {**spoofed, **cookie}
                if wrong_origin is not None:
                    headers['Origin'] = wrong_origin
                self.assertEqual(self.raw_request('POST', '/enable', form, headers, send_origin=False)[0], 403)
                self.assertEqual(self.sign_in('alice', headers=headers, send_origin=False)[0], 403)

        headers = {**cookie, 'Origin': origin, 'Forwarded': 'host=other.test;proto=http',
                   'X-Forwarded-Host': 'other.test', 'X-Forwarded-Proto': 'http'}
        self.assertEqual(self.raw_request('POST', '/enable', form, headers)[0], 303)

    def test_public_origin_rejects_non_dns_or_non_bare_https_urls(self):
        for origin in ('', 'http://inventory.example.test', 'https://user@inventory.example.test',
                       'https://inventory.example.test/', 'https://inventory.example.test?',
                       'https://inventory.example.test#', 'https://inventory.example.test:443',
                       'https://127.0.0.1', 'https://[::1]', 'https://0x7f000001',
                       'https://under_score.test', 'https://-invalid.test', 'https://invalid..test',
                       'https://inventory.example.test.', 'https://inventory.example.test\n',
                       'https://' + 'a' * 64 + '.test'):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, '--public-origin'):
                self.start(public_origin=origin)

    def test_source_metadata_rejects_invalid_values_and_unconfigured_incus_scope(self):
        source = self.config['sources']['stagelight']
        for fields in (
            {'network_domain': ''}, {'network_domain': None}, {'network_domain': 1},
            {'network_domain': ' padding '}, {'network_domain': 'x' * 129},
            {'placement': None}, {'placement': {'scope': _STAGE}},
            {'placement': {'scope': _STAGE, 'name': 'bad\nname'}},
            {'placement': {'scope': _STAGE, 'name': 'guest', 'extra': True}},
            {'placement': {'scope': 'not/approved', 'name': 'guest'}},
        ):
            with self.subTest(fields=fields):
                self.config['sources']['stagelight'] = dict(source, **fields)
                with self.assertRaises(ValueError):
                    self.start()

    def test_queries_are_decoded_and_bounded(self):
        self.capture('stagelight', 'complete', 0, _instance('query-guest'), _instance('other-guest'))
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.collect('stagelight', csrf)
        html = self.workspace('/incus?source=%73tagelight&section=instances&item=%71uery-guest')
        self.assertIn('query-guest', html)
        self.assertNotIn('other-guest', html)
        for query in ('q=%FF', 'q=%', 'q=%00', 'q=a&%71=b', 'q=' + 'x' * 257,
                      'q=' + 'x' * 2048, '&'.join(f'q{i}=x' for i in range(9))):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', '/incus?' + query)[0], 400)

    def test_scope_filtering_personal_enablement_and_view_only_grant(self):
        self.capture('stagelight', 'complete', 0, _instance('stage-secret'))
        self.capture('shared', 'complete', 0, _instance('shared-sighting'))
        self.config['sources']['stagelight']['declarations'] = dict(
            source='synthetic declarations', scope=_STAGE, timestamp='2025-01-01T09:00:00Z',
            containers=[dict(name='unseen-declaration')], vms=[],
        )
        self.start()
        alice, bob = self.workspace(), self.workspace(user='bob')
        self.assertNotIn(_BOOTH, alice)
        self.assertNotIn('soundbooth', alice)
        self.assertNotIn(_STAGE, bob)
        self.assertNotIn('stagelight', bob)
        self.assertNotIn(_STAGE, self.workspace('/access', 'bob'))
        self.assertNotIn('unseen-declaration', self.incus('comparison'))
        alice_csrf, bob_csrf = self.field(alice, 'csrf'), self.field(bob, 'csrf')
        for action in ('enable', 'disable', 'preview', 'collect'):
            fields = {'preview': 'X' * 43} if action == 'collect' else {}
            self.assertEqual(self.post(action, 'stagelight', bob_csrf, 'bob', **fields)[0], 404)
        self.assertEqual(self.post('enable', 'not-configured', bob_csrf, 'bob')[0], 404)
        bob_overview = self.workspace('/', 'bob')
        self.assertEqual(self.post('enable', 'stagelight', alice_csrf)[0], 303)
        self.collect('stagelight', alice_csrf)
        self.assertIn('stage-secret', self.incus())
        self.assertIn('unseen-declaration', self.incus('comparison'))
        self.assertNotIn('stage-secret', self.incus(user='bob'))
        self.assertNotIn('unseen-declaration', self.incus('comparison', user='bob'))
        self.assertEqual(self.workspace('/', 'bob'), bob_overview)
        self.assertEqual(self.post('enable', 'shared', alice_csrf)[0], 303)
        self.collect('shared', alice_csrf)
        self.assertNotIn('shared-sighting', self.incus(source='shared', user='bob'))
        self.assertEqual(self.post('enable', 'shared', bob_csrf, 'bob')[0], 303)
        self.assertIn('shared-sighting', self.incus(source='shared', user='bob'))
        self.assertEqual(self.post('preview', 'shared', bob_csrf, 'bob')[0], 403)
        self.assertEqual(self.post('disable', 'shared', alice_csrf)[0], 303)
        self.assertNotIn('shared-sighting', self.incus(source='shared'))
        self.assertIn('shared-sighting', self.incus(source='shared', user='bob'))
        self.assertEqual(self.post('enable', 'shared', alice_csrf)[0], 303)
        self.assertIn('shared-sighting', self.incus(source='shared'))

    def test_placement_links_require_enabled_views_of_both_scopes(self):
        self.config['sources']['guest'] = dict(
            provider='linux', scope=_SHARED, read='Offline synthetic guest.', config={}, import_only=True,
            network_domain='theatre/lan A', placement={'scope': _STAGE, 'name': 'placed-guest'},
        )
        self.capture('stagelight', 'complete', 0, _instance('placed-guest'))
        self.start()
        alice_csrf = self.field(self.workspace(), 'csrf')
        bob_csrf = self.field(self.workspace(user='bob'), 'csrf')
        for user, csrf in (('alice', alice_csrf), ('bob', bob_csrf)):
            self.assertEqual(self.post('enable', 'guest', csrf, user)[0], 303)
        self.assertNotIn('placed-guest', self.workspace('/machines?source=guest'))
        self.assertEqual(self.post('enable', 'stagelight', alice_csrf)[0], 303)
        self.collect('stagelight', alice_csrf)
        target = '/incus?source=stagelight&amp;section=instances&amp;item=placed-guest'
        self.assertIn(target, self.workspace('/machines?source=guest'))
        self.assertIn('placed-guest', self.workspace('/relationships?source=guest'))
        for path in ('/machines?source=guest', '/relationships?source=guest'):
            html = self.workspace(path, 'bob')
            self.assertNotIn('placed-guest', html)
            self.assertNotIn(_STAGE, html)

    def test_collection_requires_origin_csrf_and_single_use_source_bound_preview(self):
        self.capture('stagelight', 'complete', 0, _instance('<img src=x onerror=alert(1)>'))
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        form = dict(csrf=csrf, source='stagelight')
        for origin in ('', 'null', 'http://127.0.0.1:1', 'https://example.invalid'):
            self.assertEqual(self.request('POST', '/enable', fields=form, headers={'Origin': origin})[0], 403)
        self.assertEqual(self.post('enable', 'stagelight', 'wrong-token')[0], 403)
        self.assertEqual(self.post('preview', 'stagelight', csrf)[0], 403)
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.assertEqual(self.post('enable', 'shared', csrf)[0], 303)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview='X' * 43)[0], 403)
        self.assertEqual(self.request('GET', '/collect')[0], 404)
        preview = self.preview('stagelight', csrf)
        self.assertIn(_STAGE, preview)
        self.assertIn('saved &lt;operator&gt; snapshot', preview)
        self.assertNotIn(str(self.root), preview)
        self.assertIn('inventory.sqlite3', preview)
        self.assertNotIn('img src=x', self.incus())
        token = self.field(preview, 'preview')
        other = {'Cookie': self.signed_in_cookie('alice')}
        other_csrf = self.field(self.raw_request('GET', '/', headers=other)[2], 'csrf')
        confirmation = dict(source='stagelight', csrf=other_csrf, preview=token)
        self.assertEqual(self.raw_request('POST', '/collect', confirmation, other)[0], 403)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token,
                                   snapshot_path='/unexpected')[0], 400)
        self.assertEqual(self.post('collect', 'shared', csrf, preview=token)[0], 403)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token)[0], 303)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token)[0], 403)
        inventory = self.incus()
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', inventory)
        self.assertNotIn('<img src=x onerror=alert(1)>', inventory)

    def test_complete_inventory_can_establish_absence_but_incomplete_attempts_cannot(self):
        self.config['sources']['stagelight']['declarations'] = dict(
            source='synthetic declarations', scope=_STAGE, timestamp='2025-01-01T09:00:00Z',
            containers=[dict(name='paper-moon', cpu='4'), dict(name='tin-piano')], vms=[],
        )
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.assertIn('tin-piano', self.incus('comparison', status='not-connected'))

        self.capture('stagelight', 'partial', 0, _instance('partly-seen'))
        self.collect('stagelight', csrf)
        self.assertIn('partly-seen', self.incus())
        self.assertIn('tin-piano', self.incus('comparison', status='unknown'))
        self.assertNotIn('tin-piano', self.incus('comparison', status='absent'))

        self.capture('stagelight', 'complete', 1, _instance('paper-moon'))
        self.collect('stagelight', csrf)
        self.assertIn('paper-moon', self.incus('comparison', status='disagreement'))
        self.assertIn('tin-piano', self.incus('comparison', status='absent'))
        self.assertIn('2025-01-01T10:01:00Z', self.incus('comparison'))

        self.capture('stagelight', 'failed', 2)
        self.collect('stagelight', csrf)
        self.assertIn('paper-moon', self.incus('comparison', status='stale'))
        self.assertIn('tin-piano', self.incus('comparison', status='unknown'))
        self.assertNotIn('tin-piano', self.incus('comparison', status='absent'))
        self.assertIn('2025-01-01T10:01:00Z', self.incus())

        self.server.shutdown()
        self.start()
        self.assertIn('paper-moon', self.incus('comparison', status='stale'))
        self.capture('stagelight', 'complete', 0, _instance('obsolete'))
        self.collect('stagelight', self.field(self.workspace(), 'csrf'))
        self.assertIn('paper-moon', self.incus('comparison', status='stale'))
        self.assertNotIn('obsolete', self.incus())
        self.assertNotIn('tin-piano', self.incus('comparison', status='absent'))

    def test_runtime_sightings_do_not_cover_the_declared_instance_population(self):
        source = self.config['sources']['stagelight']
        source['config'] = dict(metrics_path=str(Path(__file__).with_name('examples') / 'runtime.prom'),
                                metrics_timestamp='2025-01-01T10:00:00Z')
        source['declarations'] = dict(source='synthetic declarations', scope=_STAGE,
                                      timestamp='2025-01-01T09:00:00Z',
                                      containers=[dict(name='velvet-curtain')], vms=[])
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.collect('stagelight', csrf)
        html = self.incus('runtime', status='partial')
        self.assertIn('trapdoor', html)
        self.assertNotIn('paper-moon', html)  # The same capture also contains a different project.
        self.assertIn('velvet-curtain', self.incus('comparison', status='unknown'))
        self.assertNotIn('velvet-curtain', self.incus('comparison', status='absent'))

    def test_provider_error_preserves_evidence_without_echoing_private_path(self):
        self.capture('stagelight', 'complete', 0, _instance('retained-sighting'))
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.collect('stagelight', csrf)
        Path(self.config['sources']['stagelight']['config']['snapshot_path']).unlink()
        self.collect('stagelight', csrf)
        html = self.incus(status='stale')
        self.assertIn('Collection failed', html)
        self.assertIn('retained-sighting', html)
        self.assertNotIn(str(self.root), html)

    def test_import_only_linux_paginates_packages_and_never_collects(self):
        scope = 'theatre/paper-moon'
        self.config['sources']['guest'] = dict(provider='linux', scope=scope, import_only=True,
                                               read='Offline guest report only.', config={})
        for key in ('view_scopes', 'collect_scopes'):
            self.config['users']['alice'][key].append(scope)
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'guest', csrf)[0], 303)
        packages = [dict(asset='paper-moon', manager='dpkg', name=f'synthetic-pkg-{i:04d}',
                         architecture='all', version='1.0') for i in range(1250)]
        memory_time = '2025-01-01T11:03:00+01:00'
        report = _envelope('linux', scope, 5,
                           dict(section='packages', outcome='complete', records=packages),
                           dict(section='cpu', outcome='complete',
                                records=[dict(view='procfs', sample_seconds=0.2, busy_percent=0)]),
                           dict(section='memory', outcome='complete', timestamp=memory_time,
                                records=[dict(view='cgroup-v2', used_bytes=0, limit_bytes=None)]),
                           dict(section='interfaces', outcome='failed', records=[]))
        self.import_ok('guest', report)
        self.assertNotIn('action="/preview"', self.workspace())
        for action in ('preview', 'collect'):
            fields = {'preview': 'X' * 43} if action == 'collect' else {}
            self.assertEqual(self.post(action, 'guest', csrf, **fields)[0], 403)
        self.assertEqual(self.request('GET', '/import-evidence')[0], 404)
        self.assertEqual(self.post('import-evidence', 'guest', csrf, file='/unexpected')[0], 404)
        self.assertEqual(self.post('enable', '/unexpected', csrf)[0], 404)

        packages_path = '/machines?source=guest&section=packages'
        first = self.workspace(packages_path)
        last = self.workspace(packages_path + '&page=50')
        self.assertIn('synthetic-pkg-0000', first)
        self.assertNotIn('synthetic-pkg-1249', first)
        self.assertIn('synthetic-pkg-1249', last)
        self.assertNotIn('synthetic-pkg-0000', last)
        self.assertIn('2025-01-01T10:05:00Z', first)
        cpu = self.workspace('/machines?source=guest&section=cpu')
        self.assertIn('Busy CPU time (%)', cpu)
        self.assertIn('>0<', cpu)  # Zero usage is observed, not unknown.
        memory = self.workspace('/machines?source=guest&section=memory')
        self.assertIn(memory_time, memory)
        self.assertIn('Uncapped', memory)
        self.assertNotIn('synthetic-pkg-', self.workspace(packages_path, 'bob'))
        self.assertEqual(self.post('disable', 'guest', csrf)[0], 303)
        self.assertNotIn('synthetic-pkg-', self.workspace(packages_path))
        self.assertEqual(self.post('enable', 'guest', csrf)[0], 303)
        self.assertIn('synthetic-pkg-1249', self.workspace(packages_path + '&page=50'))

    def test_imported_incus_context_survives_comparison_and_later_incomplete_evidence(self):
        self.config['sources']['stagelight']['declarations'] = dict(
            source='synthetic declarations', scope=_STAGE, timestamp='2025-01-01T09:00:00Z',
            containers=[dict(name='paper-moon', cpu='2'), dict(name='tin-piano')], vms=[],
        )
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        record = dict(name='paper-moon', kind='container', state='Running', cpu='1', memory='256MiB',
                      architecture='x86_64', location='synthetic-node', profiles=[], ephemeral=False,
                      description='', generation=0, created_at='2025-01-01T09:50:00Z')
        capture_time = '2025-01-01T10:01:00Z'
        complete = dict(section='instances', outcome='complete', records=[record], timestamp=capture_time)
        self.import_ok('stagelight', _envelope('incus', _STAGE, 10, complete))
        self.assertNotIn('synthetic-node', self.incus())
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.assertIn('paper-moon', self.incus('comparison', status='disagreement'))
        self.assertIn('tin-piano', self.incus('comparison', status='absent'))
        html = self.incus(item='paper-moon')
        for detail in ('x86_64', 'synthetic-node', '256MiB', '2025-01-01T09:50:00Z',
                       capture_time, '2025-01-01T10:10:00Z'):
            self.assertIn(detail, html)

        sightings = dict(section='instances', outcome='partial', records=[dict(name='partial-only')])
        failed = dict(section='instances', outcome='failed', records=[])
        replay = dict(complete, records=[dict(record, location='obsolete-location')])
        for minute, sections in ((11, [sightings]), (12, [dict(sightings, records=[])]),
                                 (13, [failed]), (14, []), (15, [replay])):
            with self.subTest(attempt=minute):
                self.import_ok('stagelight', _envelope('incus', _STAGE, minute, *sections))
                self.assertIn('paper-moon', self.incus('comparison', status='stale'))
                self.assertIn('tin-piano', self.incus('comparison', status='unknown'))
                self.assertNotIn('tin-piano', self.incus('comparison', status='absent'))
                html = self.incus()
                self.assertIn('synthetic-node', html)
                self.assertIn('partial-only', html)
                self.assertNotIn('obsolete-location', html)
                self.assertIn(capture_time, html)

    def test_import_rejects_nonmatching_or_malformed_envelopes_without_updating_evidence(self):
        self.start()
        csrf = self.field(self.workspace(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        record = dict(name='retained-sighting', kind='container', state='Running', cpu=None, memory=None)
        section = dict(section='instances', outcome='complete', records=[record])
        self.import_ok('stagelight', _envelope('incus', _STAGE, 0, section))
        candidate = _envelope('incus', _STAGE, 1, section)
        bad_sections = [None, [section, section], [dict(section, extra=True)],
                        [dict(section, timestamp='2025-01-01T10:01:00')],
                        [dict(section, outcome='unknown')], [dict(section, outcome='failed')],
                        [dict(section, records=['not a record'])],
                        [dict(section, records=[dict(name='malformed-instance')])],
                        [dict(section, records=[record, record])],
                        [dict(section, records=[dict(record, reading=float('inf'))])]]
        invalid = [dict(candidate, schema_version=True), dict(candidate, schema_version=2),
                   dict(candidate, source='stagelight'), dict(candidate, source='linux'),
                   dict(candidate, scope=_BOOTH), dict(candidate, timestamp='2025-01-01T10:01:00'),
                   dict(candidate, extra='synthetic-secret'),
                   json.dumps(candidate).replace('"schema_version": 1',
                                                 '"schema_version": 1, "schema_version": 1'),
                   *(dict(candidate, sections=sections) for sections in bad_sections)]
        cases = [('stagelight', item, 0o600) for item in invalid]
        cases += [('not-configured', candidate, 0o600), ('stagelight', candidate, 0o644)]
        for source, document, mode in cases:
            with self.subTest(source=source, document=document, mode=oct(mode)):
                result = self.import_report(source, document, mode=mode)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertNotIn(str(self.root), result.stderr)
                self.assertNotIn('synthetic-secret', result.stderr)
        html = self.incus(status='complete')
        self.assertIn('retained-sighting', html)
        self.assertNotIn('2025-01-01T10:01:00Z', html)


if __name__ == '__main__':
    unittest.main()
