"""Collect explicitly enabled, local Linux guest evidence without elevated access."""

import os
from pathlib import Path
import re
import socket
import sys
from time import monotonic, sleep


_PACKAGE_DATABASES = {
    'dpkg': ('/var/lib/dpkg/status', dict(Package='name', Architecture='architecture',
                                         Version='version', Status='status')),
    'apk': ('/lib/apk/db/installed', dict(P='name', A='architecture', V='version')),
}
_DPKG_STATES = {
    'not-installed', 'config-files', 'half-installed', 'unpacked', 'half-configured',
    'triggers-awaited', 'triggers-pending', 'installed',
}
_PACKAGE_FIELD = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+_~:-]*')
_CGROUP = '/sys/fs/cgroup/'


def _open(path):
    return Path(path).open(encoding='utf-8')


def _uint(text):
    if not text.isascii() or not text.isdecimal():
        raise ValueError('expected an unsigned counter')
    value = int(text)
    if value >= 2**64:
        raise ValueError('counter exceeds Linux unsigned range')
    return value


def _package_rows(stream, fields):
    row = {}
    has_lines = False
    for line in stream:
        if not line.strip():
            if has_lines:
                yield row
                row = {}
                has_lines = False
            continue
        has_lines = True
        key, _, value = line.partition(':')
        if key in fields:
            name = fields[key]
            if name in row:
                raise ValueError('duplicate package field')
            row[name] = value.strip()
    if has_lines:
        yield row


def _packages(config):
    manager = config.get('package_manager')
    if not isinstance(manager, str) or manager not in _PACKAGE_DATABASES:
        raise ValueError('no supported package database selected')
    path, fields = _PACKAGE_DATABASES[manager]
    records = {}
    with _open(path) as stream:
        for row in _package_rows(stream, fields):
            if manager == 'dpkg':
                status = row.pop('status', '').split()
                if len(status) != 3 or status[2] not in _DPKG_STATES:
                    raise ValueError('invalid dpkg status')
                if status[2] != 'installed':
                    continue
            keys = ('name', 'architecture', 'version')
            if not all(_PACKAGE_FIELD.fullmatch(row.get(key, '')) for key in keys):
                raise ValueError('invalid package identity or version')
            identity = row['name'], row['architecture']
            if identity in records:
                raise ValueError('duplicate package identity')
            records[identity] = dict(manager=manager, **row)
    return [records[key] for key in sorted(records)]


def _proc_cpu():
    with _open('/proc/stat') as stream:
        fields = stream.readline().split()
    if len(fields) < 9 or fields[0] != 'cpu':
        raise ValueError('aggregate CPU counters unavailable')
    # Guest and guest_nice time are already included in user and nice.
    return tuple(_uint(field) for field in fields[1:9])


def _cgroup_cpu():
    with _open(_CGROUP + 'cpu.stat') as stream:
        for line in stream:
            fields = line.split()
            if len(fields) == 2 and fields[0] == 'usage_usec':
                return _uint(fields[1])
    raise ValueError('cgroup CPU usage counter unavailable')


def _cpu(config):
    view = config.get('resource_view')
    if view == 'procfs':
        read = _proc_cpu
    elif view == 'cgroup-v2':
        read = _cgroup_cpu
    else:
        raise ValueError('no supported CPU accounting view selected')
    before = read()
    start = monotonic()
    sleep(0.2)
    after = read()
    elapsed = monotonic() - start
    if elapsed <= 0:
        raise ValueError('invalid CPU sample duration')
    if view == 'procfs':
        delta = [end - begin for begin, end in zip(before, after)]
        total = sum(delta)
        if min(delta) < 0 or not total:
            raise ValueError('CPU counters did not advance consistently')
        # Idle, I/O wait, and time stolen by the hypervisor are not execution.
        busy = total - delta[3] - delta[4] - delta[7]
        usage = dict(busy_percent=100 * busy / total)
    else:
        if after < before:
            raise ValueError('cgroup CPU counter decreased')
        usage = dict(cpu_cores_used=(after - before) / 1_000_000 / elapsed)
    return [dict(view=view, sample_seconds=elapsed, **usage)]


def _proc_memory():
    sizes = {}
    with _open('/proc/meminfo') as stream:
        for line in stream:
            key, _, value = line.partition(':')
            if key not in ('MemTotal', 'MemAvailable'):
                continue
            fields = value.split()
            if key in sizes or len(fields) != 2 or fields[1] != 'kB':
                raise ValueError('invalid memory counters')
            sizes[key] = _uint(fields[0]) * 1024
    total, available = sizes.get('MemTotal'), sizes.get('MemAvailable')
    if total is None or available is None or not total or available > total:
        raise ValueError('memory total or available unavailable')
    return dict(total_bytes=total, available_bytes=available, used_bytes=total - available)


def _cgroup_memory():
    with _open(_CGROUP + 'memory.current') as stream:
        used = _uint(stream.read().strip())
    with _open(_CGROUP + 'memory.max') as stream:
        limit = stream.read().strip()
    return dict(used_bytes=used, limit_bytes=None if limit == 'max' else _uint(limit))


def _memory(config):
    view = config.get('resource_view')
    if view == 'procfs':
        sizes = _proc_memory()
    elif view == 'cgroup-v2':
        sizes = _cgroup_memory()
    else:
        raise ValueError('no supported memory accounting view selected')
    return [dict(view=view, **sizes)]


def _disk(_config):
    stats = os.statvfs('/')
    if stats.f_frsize <= 0 or not 0 <= stats.f_bavail <= stats.f_bfree <= stats.f_blocks:
        raise ValueError('invalid filesystem block counts')
    return [dict(path='/', total_bytes=stats.f_blocks * stats.f_frsize,
                 used_bytes=(stats.f_blocks - stats.f_bfree) * stats.f_frsize,
                 available_bytes=stats.f_bavail * stats.f_frsize)]


def _users(_config):
    users = {}
    with _open('/etc/passwd') as stream:
        for line in stream:
            if not line.strip() or line.startswith('#'):
                continue
            # Never use the password, GECOS, home, or shell fields; no NSS lookups.
            if line.count(':') != 6:
                raise ValueError('invalid local user entry')
            name, _, uid, gid, _ = line.split(':', 4)
            if not name or not name.isprintable() or any(c.isspace() for c in name) or name in users:
                raise ValueError('invalid or duplicate local user name')
            users[name] = dict(name=name, uid=_uint(uid), gid=_uint(gid))
    return [users[name] for name in sorted(users)]


def _interfaces(_config):
    # This enumerates names and indices in the current network namespace only.
    return [dict(name=name, ifindex=index) for index, name in
            sorted(socket.if_nameindex(), key=lambda interface: interface[1])]


_COLLECTORS = dict(packages=_packages, cpu=_cpu, memory=_memory, disk=_disk,
                   users=_users, interfaces=_interfaces)


def collect(config):
    """Return section results for opted-in packs; the runner adds provenance."""
    if not isinstance(config, dict):
        raise ValueError('Linux configuration must be an object')
    packs = config.get('packs', [])
    if not isinstance(packs, list) or any(not isinstance(pack, str) or pack not in _COLLECTORS
                                         for pack in packs):
        raise ValueError('packs must be an array of supported Linux pack names')
    if len(packs) != len(set(packs)):
        raise ValueError('Linux pack names must not be repeated')
    if not packs:
        return []
    asset = config.get('asset', config.get('scope'))
    if not isinstance(asset, str) or not asset.strip():
        raise ValueError('Linux collection requires a nonblank asset or scope')

    sections = []
    for pack in packs:
        outcome, records = 'failed', []
        if sys.platform == 'linux':
            try:
                records = [dict(asset=asset, **row) for row in _COLLECTORS[pack](config)]
            except (OSError, ValueError):
                # Unreadable or malformed evidence is unknown, not an empty snapshot.
                pass
            else:
                outcome = 'complete'
        sections.append(dict(section=pack, outcome=outcome, records=records))
    return sections
