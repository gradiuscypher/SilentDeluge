"""Single-controller journal; GitHub remains authoritative for remote facts."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import sqlite3
import stat

from .config import FactoryError, require, serial


SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issues (
    number INTEGER PRIMARY KEY, open INTEGER NOT NULL, observation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    issue INTEGER NOT NULL, base TEXT NOT NULL, attempt INTEGER NOT NULL,
    policy TEXT NOT NULL, branch TEXT NOT NULL UNIQUE, phase TEXT NOT NULL,
    sha TEXT, pr INTEGER, merged_sha TEXT, error TEXT,
    pr_create_started INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(issue, base, attempt)
);
CREATE TABLE IF NOT EXISTS investigation_packets (
    issue INTEGER NOT NULL, base TEXT NOT NULL, packet TEXT NOT NULL,
    PRIMARY KEY(issue, base)
);
CREATE INDEX IF NOT EXISTS investigation_packets_base ON investigation_packets(base, issue);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, issue INTEGER NOT NULL, base TEXT NOT NULL,
    attempt INTEGER NOT NULL, phase TEXT NOT NULL, error TEXT
);
'''

ACCEPTANCE_QUESTIONS = {
    'bug': ('What expected behavior differs from the observed behavior?',
            'What regression check would demonstrate the expected behavior?'),
    'security': ('Which trust boundary and assets are affected?',
                 'What evidence would demonstrate that the boundary is enforced?'),
    'enhancement': ('What user outcome is missing and what constraints apply?',
                    'What observable behavior would satisfy the request?'),
    'documentation': ('Which guidance is missing, unclear, or out of date?',
                      'How can the guidance be checked against this source revision?'),
    'unclassified': ('Which triage category best fits the issue?',
                     'What evidence is needed to define an acceptance check?'),
}


@contextmanager
def locked_state(config):
    directory = config.state_dir
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            'state directory must be a private, owned directory')
    fd = os.open(directory / 'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FactoryError('controller busy; another invocation owns the state lock') from None
        db = sqlite3.connect(directory / 'factory.sqlite3')
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous = FULL')
            db.executescript(SCHEMA)
            state = State(db)
            with db:
                db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('target', serial(config.target)))
                require(state.metadata('target') == config.target, 'state belongs to a different GitHub target')
            yield state
        finally:
            db.close()


class State:
    def __init__(self, db):
        self.db = db

    def metadata(self, key):
        row = self.db.execute('SELECT value FROM metadata WHERE key = ?', (key,)).fetchone()
        return json.loads(row['value']) if row else None

    def observe(self, repo, base, issues, cards, *, approval_keys=()):
        at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        repo_url = f'https://github.com/{repo}'
        with self.db:
            prior = {entry['issue']: entry['kind'] for entry in self.observations()}
            for number in prior.keys() | issues.keys() | cards.keys():
                observation = serial({'kind': issues.get(number, prior.get(number, 'unclassified')),
                                      'card_statuses': cards.get(number, []), 'observed_at': at,
                                      'url': f'{repo_url}/issues/{number}'})
                self.db.execute('INSERT OR REPLACE INTO issues VALUES (?, ?, ?)',
                                (number, number in issues, observation))
            # The upstream open-issue page limit bounds packet creation without starving later issues.
            existing = {row['issue'] for row in self.db.execute(
                'SELECT issue FROM investigation_packets WHERE base = ?', (base,)
            )}
            for number, kind in issues.items():
                if number in existing or (number, base) in approval_keys:
                    continue
                packet = {'issue': number, 'base': base, 'kind': kind,
                          'url': f'{repo_url}/issues/{number}', 'commit_url': f'{repo_url}/commit/{base}',
                          'source_url': f'{repo_url}/tree/{base}',
                          'acceptance_questions': ACCEPTANCE_QUESTIONS[kind]}
                self.db.execute('INSERT INTO investigation_packets VALUES (?, ?, ?)',
                                (number, base, serial(packet)))
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('intake', serial({
                'base': base, 'at': at,
            })))

    def observations(self):
        return [{'issue': row['number'], 'open': bool(row['open']), **json.loads(row['observation'])}
                for row in self.db.execute('SELECT * FROM issues ORDER BY number')]

    def investigation_packets(self, *, issue=None, base=None):
        if issue is not None:
            rows = self.db.execute('SELECT packet FROM investigation_packets WHERE issue = ? ORDER BY base',
                                   (issue,))
        else:
            rows = self.db.execute('SELECT packet FROM investigation_packets WHERE base = ? ORDER BY issue',
                                   (base,))
        return [json.loads(row['packet']) for row in rows]

    def latest(self, issue):
        return self.db.execute('SELECT * FROM attempts WHERE issue = ? ORDER BY rowid DESC LIMIT 1',
                               (issue,)).fetchone()

    def history(self, issue):
        return [dict(row) for row in self.db.execute(
            'SELECT at, issue, base, attempt, phase, error FROM events WHERE issue = ? ORDER BY seq', (issue,)
        )]

    def reserve(self, issue, base, fingerprint):
        with self.db:
            attempt = self.db.execute('SELECT COALESCE(MAX(attempt), 0) + 1 FROM attempts '
                                      'WHERE issue = ? AND base = ?', (issue, base)).fetchone()[0]
            branch = f'silentdeluge-factory/i{issue}-{base}/a{attempt}'
            self.db.execute('INSERT INTO attempts (issue, base, attempt, policy, branch, phase) '
                            'VALUES (?, ?, ?, ?, ?, ?)',
                            (issue, base, attempt, fingerprint, branch, 'reserved'))
            self.event(issue, base, attempt, 'reserved', None)
        return self.latest(issue)

    def transition(self, run, phase, *, error=None, sha=None, pr=None, merged_sha=None):
        issue, base, attempt = (run[key] for key in ('issue', 'base', 'attempt'))
        with self.db:
            self.db.execute('UPDATE attempts SET phase = ?, error = ?, sha = COALESCE(?, sha), '
                            'pr = COALESCE(?, pr), merged_sha = COALESCE(?, merged_sha) '
                            'WHERE issue = ? AND base = ? AND attempt = ?',
                            (phase, error, sha, pr, merged_sha, issue, base, attempt))
            self.event(issue, base, attempt, phase, error)
        return self.latest(issue)

    def begin_pr_creation(self, run):
        with self.db:
            self.db.execute('UPDATE attempts SET pr_create_started = 1 WHERE issue = ? AND base = ? AND attempt = ?',
                            (run['issue'], run['base'], run['attempt']))
            self.event(run['issue'], run['base'], run['attempt'], 'pr-create-intent', None)

    def event(self, issue, base, attempt, phase, error):
        self.db.execute('INSERT INTO events (at, issue, base, attempt, phase, error) '
                        'VALUES (?, ?, ?, ?, ?, ?)',
                        (datetime.now(timezone.utc).isoformat(timespec='seconds'), issue, base, attempt,
                         phase, error))


def describe(run):
    if run is None:
        return None
    return {key: run[key] for key in ('issue', 'base', 'attempt', 'branch', 'phase', 'sha', 'pr',
                                       'merged_sha', 'error')}
