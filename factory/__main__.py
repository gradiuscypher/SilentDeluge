"""Factory CLI for read-only intake and explicitly authorized promotion."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .config import STAGES, Config, FactoryError, Policy
from .github import GitHub
from .pipeline import Pipeline, require_promotion
from .state import locked_state


COMMANDS = {'dispatch': 'implement', **{stage: stage for stage in STAGES[1:]}}


def issue_number(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 10 or not 0 < int(value) < 2**31:
        raise argparse.ArgumentTypeError('expected a positive GitHub issue number')
    return int(value)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m factory', description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--policy', type=Path, help='separately provisioned maintainer approvals')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('intake', help='read GitHub and reconcile local investigation and queue records')
    commands.add_parser('reconcile', help='advance issues through the stages granted by operator policy')
    commands.add_parser('status', help='report cached observations and journal without contacting GitHub').add_argument(
        'issue', type=issue_number, nargs='?')
    for command in (*COMMANDS, 'retry'):
        commands.add_parser(command).add_argument('issue', type=issue_number)
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        policy = Policy.load(args.policy, config) if args.policy else None
        if args.command not in ('intake', 'status'):
            require_promotion(config, policy)
        with locked_state(config) as state:
            gh = None if args.command == 'status' else GitHub(config)
            pipeline = Pipeline(config, state, gh, policy)
            if args.command == 'intake':
                pipeline.snapshot()
                result = pipeline.status()
            elif args.command == 'status':
                result = pipeline.status(args.issue)
            elif args.command == 'reconcile':
                result = pipeline.reconcile()
            elif args.command == 'retry':
                result = pipeline.retry(args.issue)
            else:
                result = pipeline.run_stage(args.issue, COMMANDS[args.command])
        print(json.dumps(result, sort_keys=True))
        if args.command == 'reconcile' and any(outcome['result'] == 'failed' for outcome in result['issues']):
            return 2
        return 0
    except FactoryError as error:
        print(f'factory: {error}', file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        print('factory: invalid settings, upstream response, or inaccessible local state', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
