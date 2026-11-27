"""Read operator-pushed Incus files; run directly to make an operator export."""

from datetime import datetime, timezone
import json
from pathlib import Path

from fields import observed_fields
from metrics_inventory import sightings


_LIMITS = ('limits.cpu', 'limits.memory')
_KINDS = ('container', 'virtual-machine')


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


def _instance_records(instances):
    if not isinstance(instances, list):
        raise ValueError('Incus instances must be an array')
    seen = set()
    records = []
    for instance in instances:
        if not isinstance(instance, dict) or not _text(instance.get('name')):
            raise ValueError('invalid Incus instance name')
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
        records.append(dict(name=name, **observed_fields(instance)))
    return records


def _snapshot(path, scope):
    try:
        with open(path, encoding='utf-8') as stream:
            export = json.load(stream, object_pairs_hook=_object)
    except (OSError, ValueError, RecursionError):
        raise ValueError('cannot read Incus snapshot JSON') from None
    if not isinstance(export, dict) or export.get('scope') != scope:
        raise ValueError('Incus snapshot scope does not match configured scope')
    timestamp = _timestamp(export.get('timestamp'), 'Incus snapshot')
    outcome = export.get('outcome')
    if outcome not in ('complete', 'partial', 'failed'):
        raise ValueError('invalid Incus snapshot outcome')
    instances = export.get('instances')
    if outcome == 'failed' and instances != []:
        raise ValueError('failed Incus snapshot must have an empty instances array')
    return dict(section='instances', outcome=outcome, timestamp=timestamp,
                records=_instance_records(instances))


def _metrics(path, scope, timestamp):
    project = _project(scope)
    timestamp = _timestamp(timestamp, 'Incus metrics capture')
    try:
        with open(path, encoding='utf-8') as stream:
            seen = sightings(stream, project)
    except (OSError, ValueError):
        raise ValueError('cannot read Incus metrics capture') from None
    return dict(section='runtime', outcome='partial', timestamp=timestamp,
                records=[dict(name=name, kind=kind, state='running')
                         for name, kind in sorted(seen.items())])


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
    instances = []
    for record in _instance_records(body.get('metadata')):
        config = {key: record[field] for key, field in zip(_LIMITS, ('cpu', 'memory'))
                  if record[field] is not None}
        instances.append(dict(name=record['name'], type=record['kind'], status=record['state'],
                              expanded_config=config))
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
