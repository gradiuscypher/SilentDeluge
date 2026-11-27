"""Build a partial inventory from a saved Incus Prometheus text capture."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from inventory import compare

# Recognize only guest runtime families, never daemon/project-wide metrics.
FAMILY = re.compile(r'^(incus_boot_time_seconds|incus_cpu_seconds_total)(?=[{\s]|$)')
LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*=\s*"((?:[^"\\]|\\["\\n])*)"\s*')
VALUE = re.compile(r'\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s+-?\d+)?\s*$')


def sightings(lines, project):
    seen = {}
    for number, line in enumerate(lines, 1):
        family = FAMILY.match(line)
        if not family:
            continue
        pos = family.end()
        labels = {}
        if line[pos:pos + 1] == '{':
            pos += 1
            while match := LABEL.match(line, pos):
                key, value = match.groups()
                if key in labels:
                    raise ValueError(f'duplicate label on line {number}')
                labels[key] = re.sub(r'\\(.)', lambda m: '\n' if m[1] == 'n' else m[1], value)
                pos = match.end()
                if line[pos:pos + 1] != ',':
                    break
                pos += 1
            if line[pos:pos + 1] != '}':
                raise ValueError(f'invalid labels on line {number}')
            pos += 1
        if not VALUE.fullmatch(line[pos:]):
            raise ValueError(f'invalid runtime sample on line {number}')
        if labels.get('project') != project:
            continue
        name, kind = labels.get('name'), labels.get('type')
        if not name or kind not in ('container', 'virtual-machine'):
            raise ValueError(f'incomplete instance identity on line {number}')
        if seen.setdefault(name, kind) != kind:
            raise ValueError(f'conflicting instance types on line {number}')
    return seen


def partial_inventory(declarations, lines, scope, source, timestamp):
    server, sep, project = scope.partition('/')
    if not server or not sep or not project or '/' in project or scope != declarations['scope']:
        raise ValueError('capture scope must match declaration server/project')
    if not source.strip() or datetime.fromisoformat(timestamp).tzinfo is None:
        raise ValueError('capture needs a source and timestamp with UTC offset')
    declared = {r['name']: r['declared'] for r in compare(declarations, [])}
    seen = sightings(lines, project)
    rows = []
    for name in sorted(declared.keys() | seen.keys()):
        kind = seen.get(name)
        expected = declared.get(name)
        rows.append(dict(name=name, declared=expected, presence='observed' if kind else 'unknown',
                         observed=dict(kind=kind, state='running', cpu=None, memory=None) if kind else None,
                         kind_disagreement=(kind != expected['kind']) if kind and expected else None))
    return dict(outcome='partial', complete_inventory='not connected',
                declaration_source={k: declarations[k] for k in ('scope', 'source', 'timestamp')},
                observation_source=dict(scope=scope, project=project, source=source, timestamp=timestamp), rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('declarations', type=Path)
    parser.add_argument('capture', type=Path)
    for arg in ('scope', 'source', 'timestamp'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    with args.capture.open() as stream:
        report = partial_inventory(json.loads(args.declarations.read_text()), stream,
                                   args.scope, args.source, args.timestamp)
    print(json.dumps(report, indent=2))
