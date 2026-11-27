"""Read operator-pushed Incus files; run directly to make an operator export."""

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import re

from fields import observed_fields
from metrics_inventory import LABEL


_LIMITS = ('limits.cpu', 'limits.memory')
_KINDS = ('container', 'virtual-machine')
_INSTANCE_DETAILS = ('architecture', 'location', 'profiles', 'ephemeral', 'created_at', 'last_used_at')
_CPU = 'incus_cpu_seconds_total'
_BOOT = 'incus_boot_time_seconds'
_TIME = 'incus_time_seconds'
_RSS = 'incus_memory_RSS_bytes'
_MEM_AVAILABLE = 'incus_memory_MemAvailable_bytes'
_MEM_TOTAL = 'incus_memory_MemTotal_bytes'
_RUNTIME_FAMILIES = {_CPU, _BOOT, _TIME, _RSS, _MEM_AVAILABLE, _MEM_TOTAL}
_FAMILY = re.compile(r'^(incus_[A-Za-z_0-9]+)(?=[{\s]|$)')
_VALUE = re.compile(r'\s+([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
                    r'|NaN|[+-]?Inf)(?:\s+-?[0-9]+)?\s*$')
_CPU_INDEX = re.compile(r'0|[1-9][0-9]{0,9}')


def _instance_details(instance):
    for key in _INSTANCE_DETAILS:
        if key not in instance:
            continue
        value = instance[key]
        if key in ('architecture', 'location'):
            if value == '':
                continue  # Incus can report an unset architecture or cluster location.
            valid = _text(value)
        elif key == 'profiles':
            valid = isinstance(value, list) and all(_text(profile) for profile in value)
        elif key == 'ephemeral':
            valid = isinstance(value, bool)
        else:
            if value == '0001-01-01T00:00:00Z':
                continue  # Incus serializes an unset Go time.Time as this sentinel.
            _timestamp(value, f'Incus instance {key}')
            valid = True
        if not valid:
            raise ValueError(f'invalid Incus instance {key}')
        yield key, value


def _text(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _project(scope):
    server, _, project = scope.partition('/')
    if not _text(server) or not _text(project) or '/' in project:
        raise ValueError('expected an Incus scope of server/project')
    return project


def _timestamp(value, label):
    try:
        if isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None:
            return value
    except ValueError:
        pass
    raise ValueError(f'{label} needs an ISO timestamp with UTC offset')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _check_project(document, project):
    if 'project' in document and (project is None or document['project'] != project):
        raise ValueError('Incus inventory project does not match configured scope')


def _instance_records(instances, project):
    if not isinstance(instances, list):
        raise ValueError('Incus instances must be an array')
    seen = set()
    records = []
    for instance in instances:
        if not isinstance(instance, dict) or not _text(instance.get('name')):
            raise ValueError('invalid Incus instance name')
        _check_project(instance, project)
        name = instance['name']
        if name in seen:
            raise ValueError('duplicate Incus instance name')
        seen.add(name)
        if instance.get('type') not in _KINDS:
            raise ValueError('invalid Incus instance kind')
        if not _text(instance.get('status')):
            raise ValueError('invalid Incus instance status')
        expanded = instance.get('expanded_config')
        if not isinstance(expanded, dict) or any(
            key in expanded and not isinstance(expanded[key], str) for key in _LIMITS
        ):
            raise ValueError('invalid Incus effective limits')
        records.append(dict(name=name, **observed_fields(dict(
            type=instance['type'], status=instance['status'], expanded_config=expanded,
        )), **dict(_instance_details(instance))))
    return records


def _snapshot(path, scope):
    try:
        with open(path, encoding='utf-8') as stream:
            export = json.load(stream, object_pairs_hook=_object)
    except (OSError, ValueError, RecursionError):
        raise ValueError('cannot read Incus snapshot JSON') from None
    if not isinstance(export, dict) or export.get('scope') != scope:
        raise ValueError('Incus snapshot scope does not match configured scope')
    project = _project(scope) if '/' in scope else None
    _check_project(export, project)
    timestamp = _timestamp(export.get('timestamp'), 'Incus snapshot')
    outcome = export.get('outcome')
    if outcome not in ('complete', 'partial', 'failed'):
        raise ValueError('invalid Incus snapshot outcome')
    instances = export.get('instances')
    if outcome == 'failed' and instances != []:
        raise ValueError('failed Incus snapshot must have an empty instances array')
    return dict(section='instances', outcome=outcome, timestamp=timestamp,
                records=_instance_records(instances, project))


def _metric_labels(line, pos):
    labels = {}
    if line[pos:pos + 1] == '{':
        pos += 1
        while match := LABEL.match(line, pos):
            key, value = match.groups()
            if key in labels:
                raise ValueError('duplicate Incus metrics label')
            labels[key] = value
            pos = match.end()
            if line[pos:pos + 1] != ',':
                break
            pos += 1
        if line[pos:pos + 1] != '}':
            raise ValueError('invalid Incus metrics labels')
        pos += 1
    return labels, pos


def _label_value(value):
    return re.sub(r'\\(.)', lambda match: '\n' if match[1] == 'n' else match[1], value)


def _remember(samples, key, value):
    if samples.setdefault(key, value) != value:
        raise ValueError('conflicting Incus runtime samples')


def _runtime_samples(lines, project):
    guests = {}
    for line in lines:
        match = _FAMILY.match(line)
        if not match or (family := match[1]) not in _RUNTIME_FAMILIES:
            continue
        labels, pos = _metric_labels(line, match.end())
        if _label_value(labels.get('project', '')) != project:
            continue
        name = _label_value(labels.get('name', ''))
        kind = labels.get('type')
        if not _text(name) or kind not in _KINDS:
            raise ValueError('incomplete Incus metrics guest identity')
        sample = _VALUE.fullmatch(line, pos)
        if not sample:
            raise ValueError('invalid Incus metrics sample')
        value = float(sample[1])
        if not isfinite(value) or value < 0:
            continue
        guest = guests.get(name)
        if guest is None:
            guest = guests[name] = (kind, {}, {})
        elif guest[0] != kind:
            raise ValueError('conflicting Incus metrics guest kinds')
        _, scalars, counters = guest
        if family == _CPU:
            # Keep measured user/system series, not an aggregate or utilization estimate.
            mode = labels.get('mode')
            cpu = labels.get('cpu')
            if mode in ('user', 'system') and (cpu is None or _CPU_INDEX.fullmatch(cpu)):
                _remember(counters, (-1 if cpu is None else int(cpu), mode), value)
        elif value.is_integer():
            _remember(scalars, family, int(value))
    return guests


def _measurements(kind, scalars, counters):
    measurements = {}
    clock = scalars.get(_TIME, 0)
    boot = scalars.get(_BOOT, 0)
    if 0 < boot <= clock:
        measurements['uptime_seconds'] = clock - boot
    if kind == 'container' and _RSS in scalars:
        measurements['memory_rss_bytes'] = scalars[_RSS]
    if (kind == 'virtual-machine' and clock > 0 and scalars.get(_MEM_TOTAL, 0) > 0
            and _MEM_AVAILABLE in scalars):
        # Without a guest clock, Incus may be reporting QEMU-side memory estimates.
        measurements['memory_available_bytes'] = scalars[_MEM_AVAILABLE]
    if counters:
        measurements['cpu_seconds_total'] = [
            dict(mode=mode, seconds=seconds) if cpu == -1 else dict(cpu_index=cpu, mode=mode, seconds=seconds)
            for (cpu, mode), seconds in sorted(counters.items())
        ]
    return measurements


def _metrics(path, scope, timestamp):
    project = _project(scope)
    timestamp = _timestamp(timestamp, 'Incus metrics capture')
    try:
        with open(path, encoding='utf-8') as stream:
            guests = _runtime_samples(stream, project)
    except (OSError, ValueError):
        raise ValueError('cannot read Incus metrics capture') from None
    capture = dict(source='incus-prometheus', scope=scope, timestamp=timestamp)
    records = []
    for name, (kind, scalars, counters) in sorted(guests.items()):
        record = dict(name=name, kind=kind, state='running')
        measurements = _measurements(kind, scalars, counters)
        if measurements:
            record.update(measurements=measurements, capture=capture)
        records.append(record)
    return dict(section='runtime', outcome='partial', timestamp=timestamp, records=records)


def collect(config):
    """Return independently dated instance inventory and/or runtime sightings."""
    scope = config.get('scope')
    if not _text(scope):
        raise ValueError('Incus collection needs a scope')
    if '/' in scope:
        _project(scope)
    snapshot = config.get('snapshot_path')
    metrics = config.get('metrics_path')
    for path, label in ((snapshot, 'snapshot_path'), (metrics, 'metrics_path')):
        if label in config and not _text(path):
            raise ValueError(f'Incus {label} must be a non-empty path')
    if metrics is None and 'metrics_timestamp' in config:
        raise ValueError('Incus metrics_timestamp requires metrics_path')
    if snapshot is None and metrics is None:
        raise ValueError('Incus collection needs snapshot_path or metrics_path')
    sections = []
    if snapshot is not None:
        sections.append(_snapshot(snapshot, scope))
    if metrics is not None:
        sections.append(_metrics(metrics, scope, config.get('metrics_timestamp')))
    return sections


def _export_document(body, scope, timestamp):
    if not isinstance(body, dict) or body.get('type') != 'sync' or body.get('status_code') != 200:
        raise ValueError('Incus inventory response was not successful')
    project = _project(scope)
    _check_project(body, project)
    instances = []
    for record in _instance_records(body.get('metadata'), project):
        config = {key: record[field] for key, field in zip(_LIMITS, ('cpu', 'memory'))
                  if record[field] is not None}
        instances.append(dict(name=record['name'], type=record['kind'], status=record['state'],
                              expanded_config=config,
                              **{key: record[key] for key in _INSTANCE_DETAILS if key in record}))
    return dict(source='incus-operator-export', scope=scope, timestamp=timestamp,
                outcome='complete', instances=instances)


def _export(remote, scope, output):
    # Only the explicit operator CLI invokes Incus. Imported collection is offline.
    import subprocess
    from tempfile import NamedTemporaryFile
    from urllib.parse import urlencode

    if not _text(remote) or remote.startswith('-') or ':' in remote or '/' in remote:
        raise ValueError('expected an explicit Incus remote alias')
    query = urlencode({'recursion': 1, 'project': _project(scope)})
    target = f'{remote}:/1.0/instances?{query}'
    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        response = subprocess.run(['incus', 'query', target, '--raw'], check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        body = json.loads(response.stdout, object_pairs_hook=_object)
    except (OSError, ValueError, RecursionError, subprocess.CalledProcessError):
        raise ValueError('Incus inventory read failed') from None
    export = _export_document(body, scope, timestamp)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(export, stream, indent=2)
            stream.write('\n')
        temporary.replace(output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _main():
    import argparse

    parser = argparse.ArgumentParser(description='Operator-only whole-project Incus inventory export.')
    parser.add_argument('--remote', required=True, help='pre-approved Incus client remote alias')
    parser.add_argument('--scope', required=True, help='approved server/project')
    parser.add_argument('--output', required=True, type=Path, help='private destination JSON file')
    parser.add_argument('--confirm-whole-project', required=True, action='store_true',
                        help='confirm that the remote and identity cover the entire approved project')
    args = parser.parse_args()
    try:
        _export(args.remote, args.scope, args.output)
    except ValueError as error:
        parser.exit(2, f'Incus export error: {error}\n')
    except OSError:
        parser.exit(2, 'Incus export error: cannot write snapshot\n')


if __name__ == '__main__':
    _main()
