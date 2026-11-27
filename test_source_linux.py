"""linux.explicit-local-packs: only opted-in packs read local guest evidence.

linux.unavailable-is-unknown: unsupported, unreadable, or malformed evidence
must not be returned as a successful empty inventory.
"""

from collections import deque
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from source_linux import collect


class LocalLinuxInventory(unittest.TestCase):
    def setUp(self):
        self.inputs = {}
        self.open = self.enterContext(patch('source_linux.Path.open', autospec=True,
                                            side_effect=self.open_fixture))
        self.platform = self.enterContext(patch('source_linux.sys', platform='linux'))
        self.disk = self.enterContext(patch('source_linux.os.statvfs', create=True,
                                            side_effect=AssertionError('unexpected disk read')))
        self.interfaces = self.enterContext(patch('source_linux.socket.if_nameindex',
                                                  side_effect=AssertionError('unexpected interface read')))
        self.enterContext(patch('source_linux.sleep'))
        self.clock = self.enterContext(patch('source_linux.monotonic', side_effect=(15, 15.25)))

    def open_fixture(self, path, **_options):
        text = self.inputs[str(path)]
        if isinstance(text, deque):
            text = text.popleft()
        if isinstance(text, OSError):
            raise text
        return StringIO(text)

    def run_packs(self, *packs, **options):
        return collect(dict(scope='toy-theatre/paper-moon', packs=list(packs), **options))

    def test_collection_requires_explicit_pack_selection(self):
        self.assertEqual(collect({'scope': 'toy-theatre/paper-moon'}), [])
        self.assertEqual(self.run_packs(), [])
        with self.assertRaisesRegex(ValueError, 'packs must be an array') as error:
            self.run_packs('cpu', 'synthetic-label-not-for-errors')
        self.assertNotIn('synthetic-label-not-for-errors', str(error.exception))
        self.open.assert_not_called()
        self.disk.assert_not_called()
        self.interfaces.assert_not_called()
        self.clock.assert_not_called()

    def test_dpkg_keeps_only_installed_package_identity_and_version(self):
        self.inputs['/var/lib/dpkg/status'] = (
            'Package: toy-lights\nStatus: deinstall ok config-files\n\n'
            'Package: libtoy\nStatus: hold ok installed\nArchitecture: amd64\n'
            'Version: 2:1.3-1~test\nDescription: synthetic metadata\n'
            ' Package: continuation, not a field\n\n'
            'Package: libtoy\nStatus: install ok installed\nArchitecture: i386\n'
            'Version: 2:1.3-1~test\n'
        )
        self.assertEqual(self.run_packs('packages', asset='paper-moon', package_manager='dpkg'), [{
            'section': 'packages', 'outcome': 'complete', 'records': [
                dict(asset='paper-moon', manager='dpkg', name='libtoy', architecture='amd64',
                     version='2:1.3-1~test'),
                dict(asset='paper-moon', manager='dpkg', name='libtoy', architecture='i386',
                     version='2:1.3-1~test'),
            ],
        }])

    def test_apk_reads_the_installed_database_not_package_file_lists(self):
        self.inputs['/lib/apk/db/installed'] = (
            'C:synthetic-checksum\nP:libpaper\nV:1.2-r0\nA:x86_64\n'
            'F:usr/lib\nR:libpaper.so\n'
        )
        result, = self.run_packs('packages', package_manager='apk')
        self.assertEqual(result, dict(section='packages', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', manager='apk', name='libpaper',
                 architecture='x86_64', version='1.2-r0'),
        ]))

    def test_procfs_reports_sampled_cpu_and_memory_usage(self):
        self.inputs['/proc/stat'] = deque([
            'cpu 100 10 50 500 20 5 5 10 20 2\n',
            'cpu 120 10 60 555 25 5 10 15 35 2\n',
        ])
        self.inputs['/proc/meminfo'] = 'MemTotal: 2048 kB\nMemFree: 32 kB\nMemAvailable: 768 kB\n'
        cpu, memory = self.run_packs('cpu', 'memory', resource_view='procfs')
        self.assertEqual(cpu, dict(section='cpu', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', view='procfs', sample_seconds=0.25,
                 busy_percent=35.0),
        ]))
        self.assertEqual(memory, dict(section='memory', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', view='procfs', total_bytes=2_097_152,
                 available_bytes=786_432, used_bytes=1_310_720),
        ]))

    def test_cgroup_cpu_can_use_multiple_cores_and_memory_usage_is_not_a_limit(self):
        self.inputs.update({
            '/sys/fs/cgroup/cpu.stat': deque(['usage_usec 1000000\n', 'usage_usec 1750000\n']),
            '/sys/fs/cgroup/memory.current': '3145728\n',
            '/sys/fs/cgroup/memory.max': 'max\n',
        })
        cpu, memory = self.run_packs('cpu', 'memory', resource_view='cgroup-v2')
        self.assertEqual(cpu, dict(section='cpu', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', view='cgroup-v2', sample_seconds=0.25,
                 cpu_cores_used=3.0),
        ]))
        self.assertEqual(memory, dict(section='memory', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', view='cgroup-v2', used_bytes=3_145_728,
                 limit_bytes=None),
        ]))

    def test_disk_users_and_interfaces_have_fixed_fields(self):
        self.disk.side_effect = None
        self.disk.return_value = SimpleNamespace(f_frsize=4096, f_blocks=100,
                                                 f_bfree=30, f_bavail=25)
        self.interfaces.side_effect = None
        self.interfaces.return_value = [(7, 'eth-toy'), (1, 'lo')]
        self.inputs['/etc/passwd'] = 'operator:fake-hash:1000:100:Stage prop:/home/toy:/bin/sh\n'
        disk, users, interfaces = self.run_packs('disk', 'users', 'interfaces')
        self.assertEqual(disk, dict(section='disk', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', path='/', total_bytes=409_600,
                 used_bytes=286_720, available_bytes=102_400),
        ]))
        self.assertEqual(users, dict(section='users', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', name='operator', uid=1000, gid=100),
        ]))
        self.assertEqual(interfaces, dict(section='interfaces', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', name='eth-toy', ifindex=7),
            dict(asset='toy-theatre/paper-moon', name='lo', ifindex=1),
        ]))
        self.disk.assert_called_once_with('/')

    def test_unsupported_data_sources_are_unknown_without_fallback(self):
        results = self.run_packs('packages', 'cpu', 'memory', package_manager='rpm')
        self.assertEqual(results, [dict(section=pack, outcome='failed', records=[])
                                   for pack in ('packages', 'cpu', 'memory')])
        self.platform.platform = 'darwin'
        self.assertEqual(self.run_packs('users'), [dict(section='users', outcome='failed', records=[])])
        self.open.assert_not_called()

    def test_invalid_sections_do_not_erase_an_independent_complete_empty_inventory(self):
        self.inputs['/lib/apk/db/installed'] = ''
        self.inputs['/etc/passwd'] = PermissionError('synthetic denied detail')
        self.inputs['/proc/stat'] = deque([
            'cpu 100 0 0 500 0 0 0 0\n',
            'cpu 90 0 0 550 0 0 0 0\n',
        ])
        self.inputs['/proc/meminfo'] = 'MemTotal: 1024 kB\nMemFree: 512 kB\n'
        self.assertEqual(
            self.run_packs('users', 'cpu', 'packages', 'memory', package_manager='apk',
                           resource_view='procfs'), [
                dict(section='users', outcome='failed', records=[]),
                dict(section='cpu', outcome='failed', records=[]),
                dict(section='packages', outcome='complete', records=[]),
                dict(section='memory', outcome='failed', records=[]),
            ],
        )


if __name__ == '__main__':
    unittest.main()
