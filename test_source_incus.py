import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from source_incus import _export_document, collect


_EXAMPLES = Path(__file__).parent / 'examples'
_SCOPE = 'theatre/stage'
_STAMP = '2025-01-01T11:10:00+01:00'
_METRICS_STAMP = '2025-01-01T10:05:00Z'
_INSTANCE = dict(name='tin-piano', type='virtual-machine', status='Stopped',
                 expanded_config={'limits.cpu': '2-3', 'limits.memory': '2GiB'})


class IncusSourceTest(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.snapshot = Path(self.directory) / 'snapshot.json'
        self.config = dict(scope=_SCOPE, snapshot_path=str(self.snapshot))

    def write_snapshot(self, **overrides):
        document = dict(source='synthetic-incus', scope=_SCOPE, timestamp=_STAMP,
                        outcome='complete', instances=[])
        document.update(overrides)
        self.snapshot.write_text(json.dumps(document), encoding='utf-8')

    def test_complete_fixture_keeps_stopped_guests_and_effective_limits(self):
        sections = collect(dict(scope='toy-theatre', snapshot_path=str(_EXAMPLES / 'complete.json')))
        self.assertEqual(sections, [dict(
            section='instances', outcome='complete', timestamp='2025-01-01T10:10:00Z', records=[
                dict(name='velvet-curtain', kind='container', state='Stopped', cpu='2', memory='1024MiB'),
                dict(name='tin-piano', kind='virtual-machine', state='Running', cpu='4', memory='2GiB'),
                dict(name='painted-sun', kind='container', state='Running', cpu=None, memory=None),
            ],
        )])

    def test_only_complete_empty_snapshot_can_support_absence(self):
        for outcome in ('complete', 'partial', 'failed'):
            with self.subTest(outcome=outcome):
                self.write_snapshot(outcome=outcome)
                self.assertEqual(collect(self.config), [dict(
                    section='instances', outcome=outcome, timestamp=_STAMP, records=[],
                )])

    def test_partial_retains_positive_records_but_failed_cannot(self):
        self.write_snapshot(outcome='partial', instances=[_INSTANCE])
        self.assertEqual(collect(self.config), [dict(
            section='instances', outcome='partial', timestamp=_STAMP, records=[
                dict(name='tin-piano', kind='virtual-machine', state='Stopped', cpu='2-3', memory='2GiB'),
            ],
        )])
        self.write_snapshot(outcome='failed', instances=[_INSTANCE])
        with self.assertRaises(ValueError):
            collect(self.config)

    def test_rejects_invalid_snapshot_without_dumping_input(self):
        for overrides in (
            {'scope': 'other-server/stage'},
            {'scope': 'theatre/backstage'},
            {'timestamp': '2025-01-01T10:10:00'},
            {'timestamp': 'synthetic-secret'},
            {'timestamp': None},
            {'outcome': 'synthetic-secret'},
            {'instances': {}},
            {'instances': [dict(_INSTANCE, type='synthetic-secret')]},
            {'instances': [dict(_INSTANCE, status=None)]},
            {'instances': [dict(_INSTANCE, expanded_config=None)]},
            {'instances': [dict(_INSTANCE, expanded_config={'limits.cpu': {}})]},
            {'instances': [dict(_INSTANCE, architecture={})]},
            {'instances': [dict(_INSTANCE, location=' stage-left')]},
            {'instances': [dict(_INSTANCE, profiles=['default', {'config': 'synthetic-secret'}])]},
            {'instances': [dict(_INSTANCE, ephemeral=0)]},
            {'instances': [dict(_INSTANCE, created_at='2025-01-01T10:10:00')]},
            {'instances': [dict(_INSTANCE, last_used_at='synthetic-secret')]},
            {'instances': [dict(_INSTANCE, project='synthetic-secret')]},
            {'project': 'synthetic-secret'},
            {'instances': [_INSTANCE, dict(_INSTANCE, type='container')]},
        ):
            with self.subTest(overrides=overrides):
                self.write_snapshot(**overrides)
                with self.assertRaises(ValueError) as error:
                    collect(self.config)
                self.assertNotIn('synthetic-secret', str(error.exception))

    def test_rejects_ambiguous_json_before_claiming_complete(self):
        self.snapshot.write_text(
            '{"scope":"theatre/stage","timestamp":"2025-01-01T10:10:00Z",'
            '"outcome":"partial","outcome":"complete","instances":[]}', encoding='utf-8',
        )
        with self.assertRaises(ValueError):
            collect(self.config)

    def test_saved_metrics_are_only_project_scoped_positive_sightings(self):
        config = dict(scope=_SCOPE, metrics_path=str(_EXAMPLES / 'runtime.prom'),
                      metrics_timestamp=_METRICS_STAMP)
        self.assertEqual(collect(config), [dict(
            section='runtime', outcome='partial', timestamp=_METRICS_STAMP, records=[
                dict(name='tin-piano', kind='virtual-machine', state='running'),
                dict(name='trapdoor', kind='container', state='running'),
            ],
        )])
        self.assertEqual(collect(dict(config, scope='theatre/empty-project')), [dict(
            section='runtime', outcome='partial', timestamp=_METRICS_STAMP, records=[],
        )])

    def test_runtime_preserves_scoped_measurements_without_raw_labels(self):
        metrics = Path(self.directory) / 'measured.prom'
        metrics.write_text('''\
incus_cpu_seconds_total{project="stage",name="tin-piano",type="virtual-machine",cpu="0",mode="user",private="synthetic"} 4.25
incus_cpu_seconds_total{project="stage",name="tin-piano",type="virtual-machine",cpu="0",mode="user"} 4.25
incus_cpu_seconds_total{project="stage",name="tin-piano",type="virtual-machine",mode="system"} 0
incus_cpu_seconds_total{project="stage",name="tin-piano",type="virtual-machine",cpu="0",mode="idle"} 1000
incus_cpu_seconds_total{project="stage",name="tin-piano",type="virtual-machine",cpu="private",mode="user"} 10
incus_memory_MemAvailable_bytes{project="stage",name="tin-piano",type="virtual-machine"} 2.5e3
incus_memory_MemTotal_bytes{project="stage",name="tin-piano",type="virtual-machine"} 8192
incus_boot_time_seconds{project="stage",name="tin-piano",type="virtual-machine"} 1000
incus_time_seconds{project="stage",name="tin-piano",type="virtual-machine"} 1123
incus_memory_RSS_bytes{project="stage",name="trapdoor",type="container"} 12000
incus_memory_RSS_bytes{project="backstage",name="trapdoor",type="container"} 5000
incus_memory_RSS_bytes{name="unscoped",type="container"} 5000
incus_uptime_seconds{project="stage",name="daemon",type="container"} 50000
incus_project_usage{project="stage",name="daemon",type="container"} 50000
''', encoding='utf-8')
        capture = dict(source='incus-prometheus', scope=_SCOPE, timestamp=_METRICS_STAMP)
        self.assertEqual(collect(dict(scope=_SCOPE, metrics_path=str(metrics),
                                      metrics_timestamp=_METRICS_STAMP)), [dict(
            section='runtime', outcome='partial', timestamp=_METRICS_STAMP, records=[
                dict(name='tin-piano', kind='virtual-machine', state='running', capture=capture,
                     measurements=dict(uptime_seconds=123, memory_available_bytes=2500,
                                       cpu_seconds_total=[dict(mode='system', seconds=0),
                                                          dict(cpu_index=0, mode='user', seconds=4.25)])),
                dict(name='trapdoor', kind='container', state='running', capture=capture,
                     measurements=dict(memory_rss_bytes=12000)),
            ],
        )])

    def test_runtime_does_not_invent_missing_or_unmeasured_values(self):
        metrics = Path(self.directory) / 'unmeasured.prom'
        metrics.write_text('''\
incus_boot_time_seconds{project="stage",name="tin-piano",type="virtual-machine"} 0
incus_time_seconds{project="stage",name="tin-piano",type="virtual-machine"} 0
incus_memory_MemAvailable_bytes{project="stage",name="tin-piano",type="virtual-machine"} 1024
incus_memory_MemTotal_bytes{project="stage",name="tin-piano",type="virtual-machine"} 8192
incus_boot_time_seconds{project="stage",name="trapdoor",type="container"} 1124
incus_time_seconds{project="stage",name="trapdoor",type="container"} 1123
incus_memory_RSS_bytes{project="stage",name="trapdoor",type="container"} NaN
incus_cpu_seconds_total{project="stage",name="trapdoor",type="container",cpu="0",mode="system"} +Inf
''', encoding='utf-8')
        self.assertEqual(collect(dict(scope=_SCOPE, metrics_path=str(metrics),
                                      metrics_timestamp=_METRICS_STAMP)), [dict(
            section='runtime', outcome='partial', timestamp=_METRICS_STAMP, records=[
                dict(name='tin-piano', kind='virtual-machine', state='running'),
                dict(name='trapdoor', kind='container', state='running'),
            ],
        )])

    def test_combined_input_keeps_capture_times_and_authority_separate(self):
        self.write_snapshot(instances=[_INSTANCE])
        inventory, runtime = collect(dict(self.config, metrics_path=str(_EXAMPLES / 'runtime.prom'),
                                          metrics_timestamp=_METRICS_STAMP))
        self.assertEqual((inventory['outcome'], inventory['timestamp'], inventory['records'][0]['state']),
                         ('complete', _STAMP, 'Stopped'))
        self.assertEqual((runtime['outcome'], runtime['timestamp'], runtime['records'][0]['state']),
                         ('partial', _METRICS_STAMP, 'running'))

    def test_metrics_need_explicit_project_and_capture_time(self):
        config = dict(scope=_SCOPE, metrics_path=str(_EXAMPLES / 'runtime.prom'))
        for overrides in ({}, {'scope': 'theatre', 'metrics_timestamp': _METRICS_STAMP},
                          {'scope': 'theatre/', 'metrics_timestamp': _METRICS_STAMP},
                          {'metrics_timestamp': '2025-01-01T10:05:00'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                collect(dict(config, **overrides))

    def test_operator_export_round_trips_only_allowlisted_inventory_fields(self):
        details = dict(architecture='x86_64', location='stage-left', profiles=['default', 'music'],
                       ephemeral=False, created_at='2025-01-01T09:30:00Z',
                       last_used_at='2025-01-01T11:00:00+01:00')
        response = dict(type='sync', status_code=200, metadata=[dict(
            _INSTANCE, **details, project='stage', config={'limits.cpu': '1', 'user.private': 'synthetic-local'},
            expanded_config=dict(_INSTANCE['expanded_config'], **{'user.private': 'synthetic-inherited'}),
            devices={'synthetic-device': {'path': '/synthetic'}}, description='synthetic-description',
        )])
        document = _export_document(response, _SCOPE, _STAMP)
        self.assertEqual(document, dict(source='incus-operator-export', scope=_SCOPE, timestamp=_STAMP,
                                        outcome='complete', instances=[dict(_INSTANCE, **details)]))
        self.snapshot.write_text(json.dumps(document), encoding='utf-8')
        self.assertEqual(collect(self.config), [dict(
            section='instances', outcome='complete', timestamp=_STAMP, records=[
                dict(name='tin-piano', kind='virtual-machine', state='Stopped',
                     cpu='2-3', memory='2GiB', **details),
            ],
        )])

    def test_operator_export_handles_unset_optional_fields_without_defaults(self):
        response = dict(type='sync', status_code=200, metadata=[
            dict(name='tin-piano', type='virtual-machine', status='Stopped', expanded_config={},
                 location='', profiles=[], last_used_at='0001-01-01T00:00:00Z'),
        ])
        self.assertEqual(_export_document(response, _SCOPE, _STAMP)['instances'], [
            dict(name='tin-piano', type='virtual-machine', status='Stopped', expanded_config={}, profiles=[]),
        ])

    def test_operator_export_rejects_a_returned_guest_from_another_project(self):
        response = dict(type='sync', status_code=200, metadata=[
            dict(_INSTANCE, project='stage'),
            dict(_INSTANCE, name='trapdoor', project='synthetic-wrong-project'),
        ])
        with self.assertRaisesRegex(ValueError, '^Incus inventory project does not match configured scope$'):
            _export_document(response, _SCOPE, _STAMP)

    def test_export_error_or_missing_inventory_is_not_an_empty_success(self):
        for response in (dict(type='error', status_code=403, metadata=[]),
                         dict(type='sync', status_code=200),
                         dict(type='sync', status_code=200, metadata=[
                             dict(name='tin-piano', type='virtual-machine', status='Error'),
                         ]),
                         dict(type='sync', status_code=200, metadata=[
                             _INSTANCE,
                             dict(name='trapdoor', type='container', status='Stopped'),
                         ])):
            with self.subTest(response=response), self.assertRaises(ValueError):
                _export_document(response, _SCOPE, _STAMP)


if __name__ == '__main__':
    unittest.main()
