from datetime import datetime
from io import StringIO
import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from source_incus import _export, _export_document, _main, collect


_EXAMPLES = Path(__file__).parent / 'examples'
_SCOPE = 'theatre/stage'
_STAMP = '2025-01-01T11:10:00+01:00'
_METRICS_STAMP = '2025-01-01T10:05:00Z'
_INSTANCE = dict(name='tin-piano', type='virtual-machine', status='Stopped',
                 expanded_config={'limits.cpu': '2-3', 'limits.memory': '2GiB'})
_ADDRESS_STAMP = '2025-01-01T10:11:00Z'


def _incus_response(metadata, **envelope):
    body = dict(type='sync', status_code=200, metadata=metadata)
    body.update(envelope)
    return CompletedProcess([], 0, json.dumps(body).encode())


def _running_state(network):
    return dict(status='Running', status_code=103, network=network)


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
            state={'network': {'toy0': {'addresses': [{'address': '192.0.2.7'}]}}},
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


    def test_operator_exports_approved_state_addresses_with_their_own_capture_time(self):
        state = Path(self.directory) / 'approved-addresses.json'
        state.write_text(json.dumps(dict(
            source='incus-instance-state', scope=_SCOPE, timestamp=_METRICS_STAMP, records=[
                dict(name='tin-piano', ip_address='2001:0DB8:0:0::7'),
                dict(name='tin-piano', ip_address='2001:db8::7'),
            ],
        )), encoding='utf-8')
        response = dict(type='sync', status_code=200, metadata=[
            dict(_INSTANCE, status='Running', expanded_config={
                **_INSTANCE['expanded_config'], 'ipv4.address': '192.0.2.99',
            }, config={'synthetic-private': 'synthetic-secret'}),
        ])
        with patch('subprocess.run') as run:
            run.return_value.stdout = json.dumps(response).encode()
            _export('toy-remote', _SCOPE, self.snapshot, guest_state_path=state)
        run.assert_called_once()
        snapshot = self.snapshot.read_text(encoding='utf-8')
        self.assertNotIn('192.0.2.99', snapshot)
        self.assertNotIn('synthetic-private', snapshot)
        inventory, addresses = collect(self.config)
        self.assertEqual(inventory['section'], 'instances')
        self.assertEqual(addresses, dict(
            section='guest_addresses', outcome='partial', timestamp=_METRICS_STAMP,
            records=[dict(name='tin-piano', ip_address='2001:db8::7')],
        ))

    def test_operator_exports_addresses_only_from_explicit_instance_state_reads(self):
        scope = 'theatre/stage+blue&green'
        second = 'guest/%?#two'
        inventory = [dict(_INSTANCE, name=name, status='Running', project='stage+blue&green',
                          state={'network': {'raw': 'synthetic-private-inventory'}})
                     for name in ('tin-piano', second, 'unselected')]
        state = _running_state({
            'toy0': dict(hwaddr='synthetic-private-mac', addresses=[
                dict(family='inet', address='192.0.2.7', netmask='24', scope='global'),
                dict(family='inet6', address='2001:0DB8:0:0::7'),
                dict(family='inet6', address='2001:db8::7'),
                dict(family='inet6', address='192.0.2.99'),
                dict(family='inet', address='192.0.2.99/24'),
                dict(family='inet6', address='fe80::1%toy0'),
                dict(family='link', address='192.0.2.99'),
                dict(family='inet', address=1),
                None,
            ]),
            'toy1': None,
            'toy2': dict(addresses={'address': '192.0.2.99'}),
        })
        state.update(pid=1234, os_info={'hostname': 'synthetic-private-hostname'})
        second_state = _running_state({'toy3': dict(addresses=[
            dict(family='inet', address='198.51.100.7'),
        ])})
        argv = ['source_incus.py', '--remote', 'toy-remote', '--scope', scope,
                '--output', str(self.snapshot), '--confirm-whole-project',
                '--guest-address-instance', 'tin-piano', '--guest-address-instance', second]
        with (
            patch('sys.argv', argv),
            patch('source_incus.datetime') as clock,
            patch('subprocess.run', side_effect=[
                _incus_response(inventory), _incus_response(state), _incus_response(second_state),
            ]) as run,
        ):
            clock.now.side_effect = [datetime.fromisoformat(stamp)
                                     for stamp in ('2025-01-01T10:10:00Z', _ADDRESS_STAMP)]
            _main()

        # Every command is an Incus API read; only the base inventory is recursive.
        self.assertEqual([query.args[0] for query in run.call_args_list], [
            ['incus', 'query', 'toy-remote:/1.0/instances?recursion=1&project=stage%2Bblue%26green', '--raw'],
            ['incus', 'query', 'toy-remote:/1.0/instances/tin-piano/state?project=stage%2Bblue%26green', '--raw'],
            ['incus', 'query', 'toy-remote:/1.0/instances/guest%2F%25%3F%23two/state?project=stage%2Bblue%26green', '--raw'],
        ])
        records = [dict(name=second, ip_address='198.51.100.7'),
                   dict(name='tin-piano', ip_address='192.0.2.7'),
                   dict(name='tin-piano', ip_address='2001:db8::7')]
        self.assertEqual(json.loads(self.snapshot.read_text(encoding='utf-8')), dict(
            source='incus-operator-export', scope=scope, timestamp='2025-01-01T10:10:00Z',
            outcome='complete', instances=[
                dict(_INSTANCE, name=name, status='Running') for name in ('tin-piano', second, 'unselected')
            ], guest_addresses=dict(source='incus-instance-state', scope=scope,
                                    timestamp=_ADDRESS_STAMP, records=records),
        ))
        inventory_section, addresses = collect(dict(self.config, scope=scope))
        self.assertEqual(inventory_section['outcome'], 'complete')
        self.assertEqual(addresses, dict(section='guest_addresses', outcome='partial',
                                         timestamp=_ADDRESS_STAMP, records=records))

    def test_default_export_queries_inventory_only(self):
        with patch('subprocess.run', return_value=_incus_response([_INSTANCE])) as run:
            _export('toy-remote', _SCOPE, self.snapshot)

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [
            'incus', 'query', 'toy-remote:/1.0/instances?recursion=1&project=stage', '--raw',
        ])
        inventory, = collect(self.config)
        self.assertEqual(inventory['records'], [
            dict(name='tin-piano', kind='virtual-machine', state='Stopped', cpu='2-3', memory='2GiB'),
        ])

    def test_cli_rejects_both_address_modes_before_querying(self):
        argv = ['source_incus.py', '--remote', 'toy-remote', '--scope', _SCOPE,
                '--output', str(self.snapshot), '--confirm-whole-project',
                '--guest-address-instance', 'tin-piano', '--guest-addresses-state', 'approved.json']
        with patch('sys.argv', argv), patch('sys.stderr', new_callable=StringIO), patch('subprocess.run') as run:
            with self.assertRaises(SystemExit) as error:
                _main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()
        self.assertFalse(self.snapshot.exists())

    def test_rejects_invalid_selection_as_a_set_before_any_state_read(self):
        running = dict(_INSTANCE, status='Running', project='stage')
        stopped = dict(_INSTANCE, name='trapdoor', project='stage')
        self.write_snapshot(instances=[_INSTANCE])
        previous = self.snapshot.read_bytes()
        for selection, inventory in (
            (('tin-piano', 'Tin-Piano'), [running, stopped]),
            (('tin-piano', ' tin-piano'), [running, stopped]),
            (('tin-piano', 'trapdoor'), [running, stopped]),
            (('tin-piano', 'missing'), [running, stopped]),
            (('tin-piano', 'tin-piano'), [running, stopped]),
            (('tin-piano', '..'), [running, dict(running, name='..')]),
            (('tin-piano', 'trapdoor'), [running, dict(stopped, status='Running', project='other')]),
        ):
            with self.subTest(selection=selection, inventory=inventory):
                with patch('subprocess.run', return_value=_incus_response(inventory)) as run:
                    with self.assertRaises(ValueError):
                        _export('toy-remote', _SCOPE, self.snapshot, guest_address_instances=selection)
                run.assert_called_once()
                self.assertEqual(self.snapshot.read_bytes(), previous)

    def test_missing_or_failed_state_never_replaces_complete_inventory_with_absence(self):
        running = dict(_INSTANCE, status='Running')
        state = _running_state({'toy0': dict(addresses=[dict(family='inet', address='192.0.2.7')])})
        secret = b'synthetic-private-state'
        for unavailable, response in (
            (True, CalledProcessError(1, ['incus'], output=secret, stderr=secret)),
            (True, TimeoutExpired(['incus'], 30, output=secret)),
            (True, CompletedProcess([], 0, secret)),
            (True, _incus_response(state, type='error')),
            (True, _incus_response(state, status_code=403)),
            (True, _incus_response(state, project='other')),
            (True, _incus_response(dict(state, project='other'))),
            (True, _incus_response([state])),
            (True, _incus_response(dict(state, network=None))),
            (True, _incus_response(dict(state, status='Stopped', status_code=102))),
            (False, _incus_response(_running_state({}))),
        ):
            with self.subTest(response=response):
                with (
                    patch('subprocess.run', side_effect=[_incus_response([running]), response]),
                    patch('sys.stdout', new_callable=StringIO) as stdout,
                    patch('sys.stderr', new_callable=StringIO) as stderr,
                ):
                    _export('toy-remote', _SCOPE, self.snapshot, guest_address_instances=['tin-piano'])
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(stderr.getvalue(), (
                    'Incus guest address warning: state unavailable for 1 selected instance(s)\n'
                    if unavailable else ''
                ))
                inventory, = collect(self.config)
                self.assertEqual(inventory['outcome'], 'complete')
                self.assertEqual(inventory['records'], [
                    dict(name='tin-piano', kind='virtual-machine', state='Running', cpu='2-3', memory='2GiB'),
                ])

    def test_failed_guest_read_still_keeps_other_selected_guest_sightings(self):
        inventory = [dict(_INSTANCE, name=name, status='Running') for name in ('tin-piano', 'trapdoor')]
        state = _running_state({'toy0': dict(addresses=[dict(family='inet', address='192.0.2.7')])})
        with (
            patch('subprocess.run', side_effect=[
                _incus_response(inventory), _incus_response(state, status_code=403), _incus_response(state),
            ]),
            patch('sys.stderr', new_callable=StringIO),
        ):
            _export('toy-remote', _SCOPE, self.snapshot, guest_address_instances=['tin-piano', 'trapdoor'])
        base, addresses = collect(self.config)
        self.assertEqual(base['outcome'], 'complete')
        self.assertEqual(addresses['outcome'], 'partial')
        self.assertEqual(addresses['records'], [dict(name='trapdoor', ip_address='192.0.2.7')])

    def test_incidentally_available_configs_and_unapproved_state_are_not_address_sightings(self):
        self.write_snapshot(instances=[dict(
            _INSTANCE, status='Running',
            expanded_config={**_INSTANCE['expanded_config'], 'ipv4.address': '192.0.2.99'},
            state={'network': {'toy0': {'addresses': [{'address': '192.0.2.7'}]}}},
        )])
        section, = collect(self.config)
        self.assertEqual(section['records'], [
            dict(name='tin-piano', kind='virtual-machine', state='Running', cpu='2-3', memory='2GiB'),
        ])

    def test_address_projection_rejects_wrong_scope_unseen_guests_and_nonfixed_fields(self):
        capture = dict(source='incus-instance-state', scope=_SCOPE, timestamp=_METRICS_STAMP,
                       records=[dict(name='tin-piano', ip_address='192.0.2.7')])
        for override in (
            dict(source='incus-config'), dict(scope='theatre/other'),
            dict(timestamp='2025-01-01T10:05:00'), dict(config={'synthetic-private': 'secret'}),
            dict(records=[dict(name='trapdoor', ip_address='192.0.2.7')]),
            dict(records=[dict(name='tin-piano', ip_address='192.0.2.7/32')]),
            dict(records=[dict(name='tin-piano', ip_address='192.0.2.7', network={'raw': 'secret'})]),
        ):
            with self.subTest(override=override):
                self.write_snapshot(instances=[dict(_INSTANCE, status='Running')],
                                    guest_addresses=dict(capture, **override))
                with self.assertRaises(ValueError) as error:
                    collect(self.config)
                self.assertNotIn('secret', str(error.exception))
        self.write_snapshot(instances=[_INSTANCE], guest_addresses=capture)
        with self.assertRaises(ValueError):
            collect(self.config)


if __name__ == '__main__':
    unittest.main()
