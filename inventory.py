"""Offline, single-project inventory. No Incus client or host access."""

from datetime import datetime

from fields import differences, observed_fields


def _evidence(document):
    return {key: document[key] for key in ('source', 'scope', 'timestamp')}


def _instant(document):
    instant = datetime.fromisoformat(document['timestamp'])
    if instant.tzinfo is None:
        raise ValueError('evidence timestamp needs a UTC offset')
    return instant


def _records(document, key):
    records = document[key]
    if not isinstance(records, list):
        raise ValueError(f'{key} must be an array')
    return records


def _add(records, name, value):
    if not isinstance(name, str) or not name or name in records:
        raise ValueError(f'empty or duplicate guest name: {name!r}')
    records[name] = value


def compare(declarations, scans):
    """Return sourced claims as of the last scan. Scope identifies one server/project.

    Only complete snapshots replace inventory. A later incomplete attempt dates
    the uncertainty; it cannot advance the timestamp of the retained evidence.
    """
    declaration_source = _evidence(declarations)
    _instant(declarations)
    declared = {}
    for group, kind in [('containers', 'container'), ('vms', 'virtual-machine')]:
        for guest in _records(declarations, group):
            limits = {key: guest[key] for key in ('cpu', 'memory') if key in guest}
            _add(declared, guest['name'], {'kind': kind, **limits})

    verified = attempt = previous = None
    observed = {}
    for scan in scans:
        evidence = _evidence(scan)
        if evidence['scope'] != declaration_source['scope']:
            raise ValueError('scan and declarations must cover the same server/project')
        instant = _instant(scan)
        if previous is not None and instant <= previous:
            raise ValueError('scan timestamps must increase')
        previous = instant
        outcome = scan['outcome']
        if outcome not in ('complete', 'partial', 'failed'):
            raise ValueError(f'unknown scan outcome: {outcome!r}')
        attempt = {**evidence, 'outcome': outcome}
        if outcome == 'complete':
            snapshot = {}
            for guest in _records(scan, 'instances'):
                _add(snapshot, guest['name'], observed_fields(guest))
            observed, verified = snapshot, evidence

    rows = []
    for name in sorted(declared.keys() | observed.keys()):
        expected, actual = declared.get(name), observed.get(name)
        drift = differences(expected, actual) if expected and actual else []
        if verified is None:
            status = 'unknown' if attempt else 'not connected'
        elif attempt['outcome'] != 'complete':
            status = 'stale'
        elif actual is None:
            status = 'absent'
        elif expected is None:
            status = 'observed and undeclared'
        else:
            status = 'disagreement' if drift else 'match'
        row = dict(name=name, status=status, declared=expected, observed=actual,
                   declaration_source=declaration_source if expected else None,
                   observation_source=verified, scan=attempt, differences=drift)
        if status == 'stale':
            row['age_seconds'] = int((previous - _instant(verified)).total_seconds())
        rows.append(row)
    return rows
