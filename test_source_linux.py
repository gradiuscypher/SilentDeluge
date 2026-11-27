"""linux.explicit-local-packs: only opted-in packs read local guest evidence.

linux.unavailable-is-unknown: unsupported, unreadable, or malformed evidence
must not be returned as a successful empty inventory.
"""

from collections import deque
from contextlib import nullcontext
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
        self.os = self.enterContext(patch('source_linux.os'))
        self.os.O_DIRECTORY, self.os.O_NOFOLLOW, self.os.O_PATH, self.os.O_RDONLY = 1, 2, 4, 0
        self.disk = self.os.statvfs
        self.disk.side_effect = AssertionError('unexpected disk read')
        self.os.uname.return_value = SimpleNamespace(
            release='6.12.3-toy', machine='aarch64', nodename='synthetic-private-hostname',
        )
        self.directories, self.descriptors, self.links, self.filesystems = {'/': ()}, {}, {}, {}
        self.os.open.side_effect = self.open_directory_fixture
        self.os.close.side_effect = self.descriptors.pop
        self.os.scandir.side_effect = lambda fd: nullcontext(iter(self.directories[self.descriptors[fd]]))
        self.os.readlink.side_effect = self.readlink_fixture
        self.os.fstatvfs.side_effect = lambda fd: self.filesystems[self.descriptors[fd]]
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

    def add_directory(self, path, entries=()):
        self.directories[path] = entries
        while path != '/':
            path = path.rpartition('/')[0] or '/'
            self.directories.setdefault(path, ())

    def open_directory_fixture(self, name, flags, *, dir_fd=None):
        self.assertEqual(flags & (self.os.O_NOFOLLOW | self.os.O_DIRECTORY), 3)
        if dir_fd is None:
            self.assertEqual(name, '/')
            path = name
        else:
            self.assertNotIn('/', name)
            path = f"{self.descriptors[dir_fd].rstrip('/')}/{name}"
        if path in self.links:
            raise NotADirectoryError(path)
        if path not in self.directories:
            raise FileNotFoundError(path)
        if isinstance(self.directories[path], OSError):
            raise self.directories[path]
        fd = max(self.descriptors, default=9) + 1
        self.descriptors[fd] = path
        return fd

    def readlink_fixture(self, name, *, dir_fd=None):
        path = name if dir_fd is None else f'{self.descriptors[dir_fd]}/{name}'
        if path not in self.links:
            raise FileNotFoundError(path)
        return self.links[path]

    @staticmethod
    def entry(name, kind='file'):
        return SimpleNamespace(name=name, is_symlink=lambda: kind == 'link',
                               is_file=lambda *, follow_symlinks: kind == 'file' and not follow_symlinks)

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
        self.os.uname.assert_not_called()
        self.os.open.assert_not_called()
        self.os.scandir.assert_not_called()
        self.os.fstatvfs.assert_not_called()
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

    def test_system_emits_only_release_kernel_and_visible_boot_context(self):
        self.inputs.update({
            '/etc/os-release': "PRETTY_NAME='Synthetic private title'\nID=toyos\nVERSION_ID=\"24.03\"\n",
            '/proc/stat': 'cpu 0 0 0 0\nbtime 1700000000\nprocesses 100\n',
            '/proc/uptime': '612.35 1004.79\n',
        })
        self.assertEqual(self.run_packs('system'), [dict(section='system', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', os_id='toyos', os_version='24.03',
                 kernel_release='6.12.3-toy', architecture='aarch64',
                 boot_time_unix_seconds=1_700_000_000, uptime_seconds=612.35),
        ])])

    def test_system_supports_a_rolling_release_but_does_not_hide_unreadable_metadata(self):
        self.inputs.update({
            '/etc/os-release': FileNotFoundError(),
            '/usr/lib/os-release': "ID='rolling-toy'\n",
            '/proc/stat': 'btime 1700000000\n',
            '/proc/uptime': '12.50 1.00\n',
        })
        result, = self.run_packs('system')
        self.assertEqual(result['outcome'], 'complete')
        self.assertIsNone(result['records'][0]['os_version'])

        self.inputs['/etc/os-release'] = PermissionError('synthetic denied detail')
        self.assertEqual(self.run_packs('system'), [dict(section='system', outcome='failed', records=[])])

    def test_storage_queries_only_scoped_mounts_and_drops_sources_and_subtrees(self):
        self.inputs['/proc/self/mountinfo'] = (
            '31 30 0:41 /@ / rw - btrfs /dev/synthetic-root rw\n'
            '32 31 8:6 / /boot/efi ro - vfat /dev/synthetic-boot ro\n'
            '33 31 0:43 / /run/secrets rw - tmpfs secret-token rw\n'
            '34 31 0:44 / /home/toy\\040person rw - ext4 /dev/synthetic-home rw\n'
            '35 31 0:45 /home/toy /var rw - ext4 /dev/synthetic-bind rw\n'
            '36 31 0:46 / /mnt/toy rw - nfs remote.test:/credentials rw\n'
        )
        self.add_directory('/boot/efi')
        self.filesystems.update({
            '/': SimpleNamespace(f_frsize=4096, f_blocks=100, f_bfree=30, f_bavail=25),
            '/boot/efi': SimpleNamespace(f_frsize=512, f_blocks=200, f_bfree=25, f_bavail=25),
        })
        result, = self.run_packs('storage')
        self.assertEqual(result, dict(section='storage', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', path='/', filesystem='btrfs',
                 total_bytes=409_600, used_bytes=286_720, available_bytes=102_400),
            dict(asset='toy-theatre/paper-moon', path='/boot/efi', filesystem='vfat',
                 total_bytes=102_400, used_bytes=89_600, available_bytes=12_800),
        ]))
        self.os.scandir.assert_not_called()

    def test_storage_does_not_probe_unsupported_or_ambiguous_mounts(self):
        for mountinfo in (
            '31 30 0:41 / / rw - nfs remote.test:/toy rw\n',
            '31 30 0:41 / / rw - xfs /dev/synthetic rw\n'
            '32 31 0:42 / / rw - overlay overlay rw\n',
            '',
        ):
            with self.subTest(mountinfo=mountinfo):
                self.inputs['/proc/self/mountinfo'] = mountinfo
                self.assertEqual(self.run_packs('storage'), [
                    dict(section='storage', outcome='failed', records=[]),
                ])
        self.os.fstatvfs.assert_not_called()

    def test_systemd_service_names_include_masks_and_do_not_follow_linked_units(self):
        self.add_directory('/etc/systemd/system', [
            self.entry('curtain.service', 'link'),
            self.entry('guest.service', 'link'),
            self.entry('toy.target'),
            self.entry('toy.target.wants', 'directory'),
        ])
        self.add_directory('/usr/lib/systemd/system', [
            self.entry('curtain.service'),
            self.entry('lights.service'),
            self.entry('spot@.service'),
            self.entry('spot@home-toy.service'),
        ])
        self.links.update({
            '/lib': 'usr/lib',
            '/etc/systemd/system/curtain.service': '/dev/null',
            '/etc/systemd/system/guest.service': '/home/toy/private.service',
        })
        result, = self.run_packs('services')
        self.assertEqual(result, dict(section='services', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', manager='systemd', name='curtain.service', state='masked'),
            dict(asset='toy-theatre/paper-moon', manager='systemd', name='guest.service', state='linked'),
            dict(asset='toy-theatre/paper-moon', manager='systemd', name='lights.service', state='installed'),
            dict(asset='toy-theatre/paper-moon', manager='systemd', name='spot@.service', state='installed'),
        ]))
        self.open.assert_not_called()

    def test_services_distinguishes_empty_systemd_directory_from_missing_or_unreadable(self):
        failed = dict(section='services', outcome='failed', records=[])
        self.assertEqual(self.run_packs('services'), [failed])
        self.add_directory('/usr/lib/systemd/system')
        self.assertEqual(self.run_packs('services'), [dict(section='services', outcome='complete', records=[])])
        self.add_directory('/etc/systemd/system', PermissionError('synthetic denied detail'))
        self.assertEqual(self.run_packs('services'), [failed])
        self.assertEqual(self.run_packs('services', service_manager='toy-init'), [failed])

    def test_openrc_reports_started_markers_only_from_the_selected_view(self):
        self.add_directory('/run/openrc/started', [self.entry('toy-daemon', 'link'), self.entry('.hidden')])
        result, = self.run_packs('services', service_manager='openrc')
        self.assertEqual(result, dict(section='services', outcome='complete', records=[
            dict(asset='toy-theatre/paper-moon', manager='openrc', name='toy-daemon', state='started'),
        ]))
        self.os.readlink.assert_not_called()
        self.open.assert_not_called()

    def test_network_links_reports_kernel_state_type_mtu_and_unsigned_byte_counters(self):
        self.interfaces.side_effect = None
        self.interfaces.return_value = [(7, 'eth-toy'), (1, 'lo')]
        for name, index, state, mtu, link_type, rx, tx in (
            ('eth-toy', 7, 'lowerlayerdown', 1500, 1, 9_223_372_036_854_775_810, 21),
            ('lo', 1, 'unknown', 65536, 772, 512, 512),
        ):
            base = f'/sys/class/net/{name}/'
            self.inputs.update({base + field: f'{value}\n' for field, value in (
                ('ifindex', index), ('operstate', state), ('mtu', mtu), ('type', link_type),
                ('statistics/rx_bytes', rx), ('statistics/tx_bytes', tx),
            )})
        result, = self.run_packs('network_links')
        self.assertEqual((result['section'], result['outcome']), ('network_links', 'complete'))
        self.assertCountEqual(result['records'], [
            dict(asset='toy-theatre/paper-moon', name='lo', ifindex=1,
                 operstate='unknown', mtu_bytes=65_536, arphrd_type=772, rx_bytes=512, tx_bytes=512),
            dict(asset='toy-theatre/paper-moon', name='eth-toy', ifindex=7,
                 operstate='lowerlayerdown', mtu_bytes=1500, arphrd_type=1,
                 rx_bytes=9_223_372_036_854_775_810, tx_bytes=21),
        ])

    def test_network_links_does_not_claim_empty_or_reuse_a_mismatched_sysfs_view(self):
        self.interfaces.side_effect = None
        self.interfaces.return_value = []
        self.assertEqual(self.run_packs('network_links'), [
            dict(section='network_links', outcome='complete', records=[]),
        ])
        self.interfaces.return_value = [(7, 'eth-toy')]
        for ifindex in ('8\n', PermissionError('synthetic denied detail')):
            self.inputs['/sys/class/net/eth-toy/ifindex'] = ifindex
            self.assertEqual(self.run_packs('network_links'), [
                dict(section='network_links', outcome='failed', records=[]),
            ])


class LinuxAddressesTest(unittest.TestCase):
    def test_opted_in_addresses_are_local_canonical_and_fixed_field(self):
        body = b'''[
            {"ifname":"toy0","address":"synthetic-mac","addr_info":[
                {"family":"inet6","local":"2001:0DB8:0:0::7","prefixlen":64,"peer":"synthetic-peer"},
                {"family":"inet","local":"192.0.2.7","broadcast":"192.0.2.255","label":"synthetic-label"},
                {"family":"inet6","local":"2001:db8::9","tentative":true},
                {"family":"inet6","local":"2001:db8::a","dadfailed":true}]},
            {"ifname":"toy1","addr_info":[{"family":"inet","local":"192.0.2.7"}]}
        ]'''
        with patch('source_linux.sys', platform='linux'), patch(
            'source_linux.subprocess.run', return_value=SimpleNamespace(stdout=body),
        ) as run:
            result, = collect(dict(scope='toy-stage', packs=['addresses']))
        self.assertEqual(run.call_args.args[0], ['ip', '-j', 'address', 'show'])
        self.assertEqual(result, dict(section='addresses', outcome='complete', records=[
            dict(asset='toy-stage', ip_address='192.0.2.7'),
            dict(asset='toy-stage', ip_address='2001:db8::7'),
        ]))

    def test_default_does_not_read_addresses_and_unavailable_is_not_empty(self):
        with patch('source_linux.sys', platform='linux'), patch(
            'source_linux.subprocess.run', side_effect=PermissionError('synthetic denial'),
        ) as run:
            self.assertEqual(collect(dict(scope='toy-stage')), [])
            run.assert_not_called()
            self.assertEqual(collect(dict(scope='toy-stage', packs=['addresses'])), [
                dict(section='addresses', outcome='failed', records=[]),
            ])

    def test_malformed_address_data_is_not_returned_as_a_complete_capture(self):
        with patch('source_linux.sys', platform='linux'), patch(
            'source_linux.subprocess.run', return_value=SimpleNamespace(stdout=b'''[
                {"addr_info":[{"family":"inet","local":"192.0.2.7"},
                              {"family":"inet","local":"2001:db8::2"}]}
            ]'''),
        ):
            self.assertEqual(collect(dict(scope='toy-stage', packs=['addresses'])), [
                dict(section='addresses', outcome='failed', records=[]),
            ])


if __name__ == '__main__':
    unittest.main()
