"""Collect baseline evidence from one configured source without declarations."""

import argparse
from datetime import datetime, timezone
from importlib import import_module
import json
from pathlib import Path


_PROVIDERS = {
    'incus': 'source_incus',
    'unifi': 'source_unifi',
    'linux': 'source_linux',
}
_REQUIRED_FIELDS = {'section', 'outcome', 'records'}
_SECTION_FIELDS = _REQUIRED_FIELDS | {'timestamp'}
_OUTCOMES = ('complete', 'partial', 'failed')


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default errors can quote arbitrary argument values.
        super().error('expected SOURCE --config PATH; see --help')


def _validate_sections(sections):
    if not isinstance(sections, list):
        raise ValueError('expected a list of sections')
    for section in sections:
        if (not isinstance(section, dict)
                or not _REQUIRED_FIELDS <= section.keys() <= _SECTION_FIELDS):
            raise ValueError('invalid section fields')
        name = section['section']
        if not isinstance(name, str) or not name.strip():
            raise ValueError('section name required')
        if section['outcome'] not in _OUTCOMES:
            raise ValueError('invalid section outcome')
        records = section['records']
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ValueError('expected a list of record objects')
        if 'timestamp' in section and datetime.fromisoformat(section['timestamp']).utcoffset() is None:
            raise ValueError('section timestamp needs a UTC offset')


def main():
    parser = _ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('source', choices=_PROVIDERS)
    parser.add_argument('--config', type=Path, required=True, help='source configuration JSON file')
    args = parser.parse_args()

    # Exception messages, paths and evidence may contain credentials. Report only the phase.
    try:
        with args.config.open(encoding='utf-8') as stream:
            config = json.load(stream)
    except (OSError, ValueError, RecursionError):
        parser.exit(2, 'collection error: could not read a JSON configuration\n')

    scope = config.get('scope') if isinstance(config, dict) else None
    if not isinstance(scope, str) or not scope.strip():
        parser.exit(2, 'collection error: configuration requires a nonblank scope string\n')

    try:
        sections = import_module(_PROVIDERS[args.source]).collect(config)
    except (Exception, SystemExit):
        parser.exit(2, 'collection error: provider could not collect evidence\n')

    timestamp = datetime.now(timezone.utc).isoformat().removesuffix('+00:00') + 'Z'
    try:
        _validate_sections(sections)
        encoded = json.dumps(dict(schema_version=1, source=args.source, scope=scope,
                                  timestamp=timestamp, sections=sections), indent=2, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        parser.exit(2, 'collection error: provider returned invalid evidence\n')
    print(encoded)


if __name__ == '__main__':
    main()
