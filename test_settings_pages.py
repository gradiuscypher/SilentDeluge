from html.parser import HTMLParser
import json
import unittest

from settings_pages import render


class _Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = set()
        self.links = set()
        self.forms = []
        self.text = []
        self.templates = []
        self._form = None
        self._code = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        attrs = dict(attrs)
        if tag == 'a':
            self.links.add(attrs['href'])
        elif tag == 'form':
            self._form = {}
            self.forms.append((attrs['method'], attrs['action'], self._form))
        elif tag in ('input', 'select', 'textarea') and self._form is not None:
            self._form[attrs['name']] = attrs.get('value')
        elif tag == 'code':
            self._code = []
        elif tag == 'br' and self._code is not None:
            self._code.append('\n')

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form = None
        elif tag == 'code':
            self.templates.append(''.join(self._code))
            self._code = None

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())
        if self._code is not None:
            self._code.append(data)


def _entry(source, provider, scope, *, enabled=False, can_collect=False, import_only=False):
    return dict(id=source, provider=provider, scope=scope, read='Approved inventory only',
                enabled=enabled, can_collect=can_collect, import_only=import_only,
                state={'private': 'not-settings-evidence'} if enabled else None,
                declarations=None, placement=None)


class SettingsPagesTest(unittest.TestCase):
    csrf = 'synthetic"<csrf>'

    def test_settings_routes_render_complete_pages_with_workspace_navigation_and_logout(self):
        expected = {'/', '/machines', '/incus', '/unifi', '/relationships',
                    '/collectors', '/access', '/patterns'}
        for path in ('/collectors', '/collectors/new', '/access'):
            with self.subTest(path=path):
                html = render(path, {}, [], 'reader', self.csrf)
                self.assertTrue(html.startswith('<!doctype html>'))
                doc = _Page(html)
                self.assertTrue({'html', 'main', 'h1'} <= doc.tags)
                self.assertTrue(expected <= doc.links)
                self.assertEqual(doc.forms, [('post', '/logout', {'csrf': self.csrf})])
        self.assertIsNone(render('/machines', {}, [], 'reader', self.csrf))

    def test_collector_actions_obey_enablement_grants_and_import_only(self):
        entries = [
            _entry('ready', 'incus', 'example/stage', enabled=True, can_collect=True),
            _entry('hidden-evidence', 'linux', 'example/guest', can_collect=True),
            _entry('view-only', 'unifi', 'example/site', enabled=True),
            _entry('offline-import', 'linux', 'example/guest', enabled=True, import_only=True),
        ]
        doc = _Page(render('/collectors', {}, entries, 'reader', self.csrf))
        self.assertCountEqual(doc.forms, [
            ('post', '/logout', {'csrf': self.csrf}),
            ('post', '/disable', {'csrf': self.csrf, 'source': 'ready'}),
            ('post', '/preview', {'csrf': self.csrf, 'source': 'ready'}),
            ('post', '/enable', {'csrf': self.csrf, 'source': 'hidden-evidence'}),
            ('post', '/disable', {'csrf': self.csrf, 'source': 'view-only'}),
            ('post', '/disable', {'csrf': self.csrf, 'source': 'offline-import'}),
        ])
        self.assertIn('/collectors/new', doc.links)
        self.assertTrue({'Denied', 'Import-only — no manual collection', 'Granted', 'Disabled'} <= set(doc.text))
        self.assertNotIn('not-settings-evidence', ' '.join(doc.text))

    def test_access_groups_only_effective_scopes_and_cannot_edit_proposed_roles(self):
        entries = [
            _entry('ready', 'incus', 'example/stage', enabled=True, can_collect=True),
            _entry('import', 'linux', 'example/stage', import_only=True),
            _entry('view-only', 'unifi', 'example/site'),
        ]
        doc = _Page(render('/access', {}, entries, 'reader', self.csrf))
        self.assertCountEqual([text for text in doc.text if text.startswith('View granted for scope:')],
                              ['View granted for scope: example/stage', 'View granted for scope: example/site'])
        self.assertTrue({'Effective access now', 'Proposal only / Not enforced', 'Denied', 'Granted',
                         'Import-only — no manual collection', 'Disabled', 'reader'} <= set(doc.text))
        self.assertTrue({'/collectors#collector-ready', '/collectors#collector-import',
                         '/collectors#collector-view-only'} <= doc.links)
        self.assertEqual(doc.forms, [('post', '/logout', {'csrf': self.csrf})])

    def test_wizard_offers_only_known_providers_and_safe_read_only_examples(self):
        providers = ('linux', 'incus', 'unifi')
        for provider in providers:
            with self.subTest(provider=provider):
                doc = _Page(render('/collectors/new', {'provider': provider}, [], 'reader', self.csrf))
                self.assertEqual({href for href in doc.links if href.startswith('/collectors/new?')},
                                 {f'/collectors/new?provider={name}' for name in providers})
                template, = doc.templates
                config = json.loads(template)
                self.assertTrue(config['scope'].strip())
                if provider == 'linux':
                    self.assertEqual(config['packs'], ['system'])
                elif provider == 'incus':
                    self.assertEqual(set(config), {'scope', 'snapshot_path'})
                    self.assertTrue(config['snapshot_path'].startswith('/'))
                else:
                    self.assertIs(config['enabled'], False)
                    self.assertRegex(config['base_url'], r'\Ahttps://[a-z0-9.-]+\.test\Z')
                    self.assertEqual(set(config), {'scope', 'enabled', 'base_url', 'site_id', 'api_key_env'})
                    self.assertRegex(config['api_key_env'], r'\A[A-Z_][A-Z0-9_]*\Z')
                self.assertIn('Read requirements', doc.text)
                self.assertEqual(doc.forms, [('post', '/logout', {'csrf': self.csrf})])
                self.assertIn('/collectors', doc.links)

    def test_unsupported_provider_and_return_url_are_not_reflected_or_followed(self):
        html = render('/collectors/new', {'provider': '<img src=x>', 'next': '//example.test',
                                         'return_to': '//example.test'}, [], 'reader', self.csrf)
        doc = _Page(html)
        self.assertIn('Unknown provider. Choose one of the listed providers.', doc.text)
        self.assertFalse(doc.templates)
        self.assertNotIn('img', doc.tags)
        self.assertNotIn('example.test', html)
        self.assertIn('/collectors', doc.links)

    def test_account_scope_and_read_descriptions_are_rendered_as_text(self):
        source = _entry('source', 'incus', 'stage <em>scope</em>')
        source['read'] = '<img src=x> export'
        doc = _Page(render('/collectors', {}, [source], '<script>reader</script>', self.csrf))
        self.assertFalse({'em', 'img', 'script'} & doc.tags)
        self.assertTrue({'stage <em>scope</em>', '<img src=x> export', '<script>reader</script>'} <= set(doc.text))
