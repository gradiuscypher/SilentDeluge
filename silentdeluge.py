"""Explain an offline inventory comparison from JSON evidence files."""

import argparse
import json
from pathlib import Path

from inventory import compare


def citation(source):
    return f"{source['source']} | {source['scope']} | {source['timestamp']}"


def explain(row):
    print(f"{row['name']}: {row['status']}")
    for role, source_key in [('declared', 'declaration_source'), ('observed', 'observation_source')]:
        source = row[source_key]
        if source is None:
            continue
        fields = row[role]
        claim = 'omitted by complete scan' if fields is None else ', '.join(
            f'{key}={value if value is not None else "unset"}' for key, value in fields.items()
        )
        print(f'  {role}: {claim} [{citation(source)}]')
    if row['differences']:
        print('  differing fields: ' + ', '.join(row['differences']))
    if row['status'] in ('stale', 'unknown'):
        scan = row['scan']
        age = f"; last evidence age {row['age_seconds']}s" if 'age_seconds' in row else ''
        print(f"  {scan['outcome']} scan: current presence unknown{age} [{citation(scan)}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('declarations', type=Path)
    parser.add_argument('scan', nargs='*', type=Path, help='scan files, oldest first')
    args = parser.parse_args()
    try:
        with args.declarations.open() as stream:
            declarations = json.load(stream)
        scans = []
        for path in args.scan:
            with path.open() as stream:
                scans.append(json.load(stream))
        rows = compare(declarations, scans)
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(2, f'inventory input error: {error}\n')
    if not rows:
        print('No inventory rows.')
    for row in rows:
        explain(row)


if __name__ == '__main__':
    main()
