import json
from pathlib import Path
import unittest

from metrics_inventory import partial_inventory


class MetricsInventoryTest(unittest.TestCase):
    def test_absence_requires_complete_scan(self):
        examples = Path(__file__).parent / 'examples'
        declarations = json.loads((examples / 'runtime-declarations.json').read_text())
        stamp = '2025-01-01T10:00:00Z'
        with (examples / 'runtime.prom').open() as stream:
            report = partial_inventory(declarations, stream, 'theatre/stage', 'saved fixture', stamp)
        self.assertEqual(report['outcome'], 'partial')
        self.assertEqual(report['observation_source']['timestamp'], stamp)
        self.assertEqual([r['name'] for r in report['rows']], ['paper-moon', 'tin-piano', 'trapdoor'])
        missing, running, extra = report['rows']
        self.assertEqual(missing['presence'], 'unknown')
        self.assertIsNone(missing['observed'])
        self.assertEqual(running['observed'], dict(kind='virtual-machine', state='running', cpu=None, memory=None))
        self.assertIsNone(extra['declared'])
        self.assertEqual(extra['presence'], 'observed')
