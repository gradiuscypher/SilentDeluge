"""Synthetic HTTP acceptance for the local inventory; no live provider reads."""

from base64 import b64encode
from hashlib import pbkdf2_hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.parse import urlencode

from web_inventory import make_server


_STAGE = 'theatre/stage'
_BOOTH = 'theatre/booth'
_SHARED = 'theatre/shared'
_PASSWORDS = {'alice': 'synthetic-alice-password', 'bob': 'synthetic-bob-password'}


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
        self.path.write_text(json.dumps(self.config), encoding='utf-8')
        self.path.chmod(0o600)
        self.server = self.enterContext(make_server(self.path, port=0, public_origin=public_origin))
        thread = Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def capture(self, source, outcome, minute, *instances):
        path = Path(self.config['sources'][source]['config']['snapshot_path'])
        path.write_text(json.dumps(dict(scope=self.config['sources'][source]['scope'],
                                        timestamp=f'2025-01-01T10:{minute:02d}:00Z',
                                        outcome=outcome, instances=instances)), encoding='utf-8')

    def request(self, method, path, user='alice', fields=None, headers=None, password=None, *, send_origin=True):
        request_headers = {}
        if user is not None:
            credentials = b64encode(f'{user}:{password or _PASSWORDS[user]}'.encode()).decode()
            request_headers['Authorization'] = f'Basic {credentials}'
        if fields is not None:
            request_headers['Content-Type'] = 'application/x-www-form-urlencoded'
            if send_origin:
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

    def dashboard(self, user='alice'):
        status, _, html = self.request('GET', '/', user)
        self.assertEqual(status, 200)
        return html

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
        self.assertEqual((status, headers['Location']), (303, '/#' + source))
        return token

    def test_authentication_host_and_empty_first_run(self):
        self.config['sources'] = {}
        self.start()
        status, headers, body = self.request('GET', '/', user=None)
        self.assertEqual(status, 401)
        self.assertIn('Basic', headers['WWW-Authenticate'])
        self.assertNotIn('No sources', body)
        self.assertEqual(self.request('GET', '/', password='wrong')[0], 401)
        self.assertEqual(self.request('GET', '/', user=None, headers={'Host': 'localhost'})[0], 400)
        self.assertIn('No sources are available in your scopes.', self.dashboard())
        self.assertEqual(self.request('GET', '/patterns', user=None)[0], 401)
        self.assertIn('Inventory patterns', self.request('GET', '/patterns')[2])
        status, headers, body = self.request('GET', '/style.css')
        self.assertEqual((status, headers.get_content_type()), (200, 'text/css'))
        self.assertIn('.status-badge', body)

    def test_public_origin_requires_exact_host_and_post_origin(self):
        authority = 'inventory.example.test'
        origin = f'https://{authority}'
        self.start(public_origin=origin)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

        spoofed = {'Forwarded': f'host={authority};proto=https',
                   'X-Forwarded-Host': authority, 'X-Forwarded-Proto': 'https',
                   'X-Forwarded-Port': '443', 'X-Forwarded-Origin': origin}
        for host in (self.origin.removeprefix('http://'), authority.upper(), f'{authority}:443'):
            with self.subTest(host=host):
                self.assertEqual(self.request('GET', '/', headers={**spoofed, 'Host': host})[0], 400)

        host_header = {'Host': authority}
        status, _, html = self.request('GET', '/', headers=host_header)
        self.assertEqual(status, 200)
        form = dict(csrf=self.field(html, 'csrf'), source='stagelight')
        for wrong_origin in (None, 'null', self.origin, f'http://{authority}',
                             f'https://{authority.upper()}', f'{origin}:443', f'{origin}/'):
            with self.subTest(origin=wrong_origin):
                headers = {**spoofed, **host_header}
                if wrong_origin is not None:
                    headers['Origin'] = wrong_origin
                self.assertEqual(self.request('POST', '/enable', fields=form, headers=headers,
                                              send_origin=False)[0], 403)

        headers = {**host_header, 'Origin': origin, 'Forwarded': 'host=other.test;proto=http',
                   'X-Forwarded-Host': 'other.test', 'X-Forwarded-Proto': 'http'}
        self.assertEqual(self.request('POST', '/enable', fields=form, headers=headers)[0], 303)

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

    def test_scope_filtering_personal_enablement_and_view_only_grant(self):
        self.capture('stagelight', 'complete', 0, _instance('stage-secret'))
        self.capture('shared', 'complete', 0, _instance('shared-sighting'))
        self.start()
        alice = self.dashboard()
        bob = self.dashboard('bob')
        self.assertNotIn(_BOOTH, alice)
        self.assertNotIn('soundbooth', alice)
        self.assertNotIn(_STAGE, bob)
        self.assertNotIn('stagelight', bob)
        alice_csrf, bob_csrf = self.field(alice, 'csrf'), self.field(bob, 'csrf')
        for action in ('enable', 'disable', 'preview', 'collect'):
            fields = {'preview': 'X' * 43} if action == 'collect' else {}
            self.assertEqual(self.post(action, 'stagelight', bob_csrf, 'bob', **fields)[0], 404)
        self.assertEqual(self.post('enable', 'not-configured', bob_csrf, 'bob')[0], 404)
        self.assertEqual(self.post('enable', 'stagelight', alice_csrf)[0], 303)
        self.collect('stagelight', alice_csrf)
        self.assertIn('stage-secret', self.dashboard())
        self.assertNotIn('stage-secret', self.dashboard('bob'))
        self.assertEqual(self.post('enable', 'shared', alice_csrf)[0], 303)
        self.collect('shared', alice_csrf)
        self.assertNotIn('shared-sighting', self.dashboard('bob'))
        self.assertEqual(self.post('enable', 'shared', bob_csrf, 'bob')[0], 303)
        self.assertIn('shared-sighting', self.dashboard('bob'))
        self.assertEqual(self.post('preview', 'shared', bob_csrf, 'bob')[0], 403)
        self.assertEqual(self.post('disable', 'shared', alice_csrf)[0], 303)
        self.assertNotIn('shared-sighting', self.dashboard())
        self.assertIn('shared-sighting', self.dashboard('bob'))
        self.assertEqual(self.post('enable', 'shared', alice_csrf)[0], 303)
        self.assertIn('shared-sighting', self.dashboard())

    def test_collection_requires_origin_csrf_and_single_use_source_bound_preview(self):
        self.capture('stagelight', 'complete', 0, _instance('<img src=x onerror=alert(1)>'))
        self.start()
        csrf = self.field(self.dashboard(), 'csrf')
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
        self.assertIn('Not connected: no collection attempted.', self.dashboard())
        token = self.field(preview, 'preview')
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token,
                                   snapshot_path='/unexpected')[0], 400)
        self.assertEqual(self.post('collect', 'shared', csrf, preview=token)[0], 403)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token)[0], 303)
        self.assertEqual(self.post('collect', 'stagelight', csrf, preview=token)[0], 403)
        inventory = self.dashboard()
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', inventory)
        self.assertNotIn('<img src=x onerror=alert(1)>', inventory)
        self.assertIn('No declarations configured.', inventory)

    def test_complete_inventory_can_establish_absence_but_incomplete_attempts_cannot(self):
        self.config['sources']['stagelight']['declarations'] = dict(
            source='synthetic declarations', scope=_STAGE, timestamp='2025-01-01T09:00:00Z',
            containers=[dict(name='paper-moon', cpu='4'), dict(name='tin-piano')], vms=[],
        )
        self.start()
        csrf = self.field(self.dashboard(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.assertIn('<td>tin-piano</td><td>not connected</td>', self.dashboard())

        self.capture('stagelight', 'partial', 0, _instance('partly-seen'))
        self.collect('stagelight', csrf)
        partial = self.dashboard()
        self.assertIn('partly-seen', partial)
        self.assertIn('<td>tin-piano</td><td>unknown</td>', partial)
        self.assertNotIn('<td>absent</td>', partial)

        self.capture('stagelight', 'complete', 1, _instance('paper-moon'))
        self.collect('stagelight', csrf)
        complete = self.dashboard()
        self.assertIn('<td>paper-moon</td><td>disagreement</td>', complete)
        self.assertIn('<td>tin-piano</td><td>absent</td>', complete)
        self.assertIn('stagelight | theatre/stage | <time datetime="2025-01-01T10:01:00Z">', complete)

        self.capture('stagelight', 'failed', 2)
        self.collect('stagelight', csrf)
        stale = self.dashboard()
        self.assertIn('Latest section outcome: failed', stale)
        self.assertIn('<td>paper-moon</td><td>stale</td>', stale)
        self.assertIn('<td>tin-piano</td><td>unknown</td>', stale)
        self.assertNotIn('<td>absent</td>', stale)
        self.assertRegex(stale, r'2025-01-01T10:01:00Z</time> \(age [0-9]+ s\)')

        self.server.shutdown()
        self.start()
        self.assertIn('<td>paper-moon</td><td>stale</td>', self.dashboard())
        self.capture('stagelight', 'complete', 0, _instance('obsolete'))
        self.collect('stagelight', self.field(self.dashboard(), 'csrf'))
        replay = self.dashboard()
        self.assertIn('no newer evidence', replay)
        self.assertIn('<td>paper-moon</td><td>stale</td>', replay)
        self.assertNotIn('obsolete', replay)
        self.assertNotIn('<td>absent</td>', replay)

    def test_runtime_sightings_do_not_cover_the_declared_instance_population(self):
        source = self.config['sources']['stagelight']
        source['config'] = dict(metrics_path=str(Path(__file__).with_name('examples') / 'runtime.prom'),
                                metrics_timestamp='2025-01-01T10:00:00Z')
        source['declarations'] = dict(source='synthetic declarations', scope=_STAGE,
                                      timestamp='2025-01-01T09:00:00Z',
                                      containers=[dict(name='velvet-curtain')], vms=[])
        self.start()
        csrf = self.field(self.dashboard(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.collect('stagelight', csrf)
        html = self.dashboard()
        self.assertIn('Partial sightings', html)
        self.assertIn('trapdoor', html)
        self.assertNotIn('paper-moon', html)  # The same capture also contains a different project.
        self.assertIn('<td>velvet-curtain</td><td>unknown</td>', html)
        self.assertNotIn('<td>absent</td>', html)

    def test_provider_error_preserves_evidence_without_echoing_private_path(self):
        self.capture('stagelight', 'complete', 0, _instance('retained-sighting'))
        self.start()
        csrf = self.field(self.dashboard(), 'csrf')
        self.assertEqual(self.post('enable', 'stagelight', csrf)[0], 303)
        self.collect('stagelight', csrf)
        Path(self.config['sources']['stagelight']['config']['snapshot_path']).unlink()
        self.collect('stagelight', csrf)
        html = self.dashboard()
        self.assertIn('Collection failed', html)
        self.assertIn('Latest section outcome: not returned', html)
        self.assertIn('Stale — last verified complete capture', html)
        self.assertIn('retained-sighting', html)
        self.assertNotIn(str(self.root), html)


if __name__ == '__main__':
    unittest.main()
