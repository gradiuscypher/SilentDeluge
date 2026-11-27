"""Collection CLI contract with synthetic providers; no declarations or live services."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class CollectionCLI(unittest.TestCase):
    def setUp(self):
        sandbox = TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.root = Path(sandbox.name)
        # Test the script with only the synthetic providers on its import path.
        self.runner = self.root / 'collection.py'
        shutil.copyfile(Path(__file__).with_name('collection.py'), self.runner)
        self.config = self.root / 'config.json'

    def provider(self, name, code):
        (self.root / f'source_{name}.py').write_text(code, encoding='utf-8')

    def invoke(self, source, config):
        self.config.write_text(json.dumps(config), encoding='utf-8')
        return self.run_cli(source, '--config', str(self.config))

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, '-E', '-S', '-B', str(self.runner), *args],
            cwd=self.root, capture_output=True, text=True, timeout=15,
        )

    def evidence(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def assert_rejected(self, result, secret, diagnostic):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn(diagnostic, result.stderr)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_each_source_can_collect_without_declarations(self):
        self.provider('incus', '''\
def collect(config):
    return [{'section': 'instances', 'outcome': 'complete',
             'records': [{'name': 'velvet-curtain', 'type': 'container', 'status': 'Stopped',
                          'expanded_config': {'limits.cpu': '2'}}],
             'timestamp': '2025-01-01T11:10:00+01:00'}]
''')
        self.provider('unifi', '''\
def collect(config):
    return [{'section': 'devices', 'outcome': 'complete',
             'records': [{'id': 'toy-switch'}]}]
''')
        self.provider('linux', '''\
def collect(config):
    return [{'section': 'packages', 'outcome': 'complete',
             'records': [{'asset': config['asset'], 'name': 'toy-library'}]}]
''')
        cases = (
            ('incus', {'scope': 'theatre/stage'}, {'section': 'instances', 'outcome': 'complete',
             'records': [{'name': 'velvet-curtain', 'type': 'container', 'status': 'Stopped',
                          'expanded_config': {'limits.cpu': '2'}}],
             'timestamp': '2025-01-01T11:10:00+01:00'}),
            ('unifi', {'scope': 'theatre/site'}, {'section': 'devices', 'outcome': 'complete',
             'records': [{'id': 'toy-switch'}]}),
            ('linux', {'scope': 'theatre/guest', 'asset': 'toy-guest'},
             {'section': 'packages', 'outcome': 'complete',
              'records': [{'asset': 'toy-guest', 'name': 'toy-library'}]}),
        )
        for source, config, section in cases:
            with self.subTest(source=source):
                envelope = self.evidence(self.invoke(source, config))
                timestamp = envelope.pop('timestamp')
                self.assertEqual(datetime.fromisoformat(timestamp).utcoffset(), timedelta(0))
                self.assertEqual(envelope, {'schema_version': 1, 'source': source,
                                            'scope': config['scope'], 'sections': [section]})

    def test_incomplete_sections_do_not_claim_complete_coverage(self):
        self.provider('unifi', '''\
def collect(config):
    return [{'section': 'devices', 'outcome': 'partial',
             'records': [{'id': 'toy-switch'}]},
            {'section': 'clients', 'outcome': 'failed', 'records': []}]
''')
        envelope = self.evidence(self.invoke('unifi', {'scope': 'theatre/site'}))
        self.assertEqual(envelope['sections'], [
            {'section': 'devices', 'outcome': 'partial', 'records': [{'id': 'toy-switch'}]},
            {'section': 'clients', 'outcome': 'failed', 'records': []},
        ])

    def test_scope_is_required(self):
        self.provider('incus', 'def collect(config):\n    return []\n')
        for scope in ({}, {'scope': '  '}, {'scope': 123}):
            with self.subTest(scope=scope):
                result = self.invoke('incus', {'credential': 'toy-secret', **scope})
                self.assert_rejected(result, 'toy-secret', 'requires a nonblank scope')

    def test_provider_exception_emits_no_evidence_or_credentials(self):
        self.provider('linux', '''\
def collect(config):
    raise RuntimeError(f"collection rejected with {config['credential']}")
''')
        result = self.invoke('linux', {'scope': 'theatre/guest', 'credential': 'toy-secret'})
        self.assert_rejected(result, 'toy-secret', 'provider could not collect evidence')

    def test_invalid_provider_result_emits_no_evidence_or_credentials(self):
        self.provider('incus', '''\
def collect(config):
    return [{'section': 'instances', 'outcome': config['credential'], 'records': []}]
''')
        result = self.invoke('incus', {'scope': 'theatre/stage', 'credential': 'toy-secret'})
        self.assert_rejected(result, 'toy-secret', 'provider returned invalid evidence')

    def test_invalid_cli_arguments_are_not_echoed(self):
        result = self.run_cli('toy-secret', '--config', str(self.config))
        self.assert_rejected(result, 'toy-secret', 'expected SOURCE --config PATH')


if __name__ == '__main__':
    unittest.main()
