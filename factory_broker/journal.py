"""One local durable journal and process fence for the configured target."""

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import stat

from .boundary import encode, need, trusted_path


SCHEMA = '''
CREATE TABLE identity (target TEXT NOT NULL);
CREATE TABLE runs (
    id INTEGER PRIMARY KEY, issue INTEGER NOT NULL, base TEXT NOT NULL, attempt INTEGER NOT NULL,
    authority TEXT NOT NULL, scope TEXT NOT NULL, UNIQUE (issue, base, attempt)
);
CREATE TABLE actions (
    id INTEGER NOT NULL REFERENCES runs(id), stage TEXT NOT NULL, sha TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'fenced')), PRIMARY KEY (id, stage)
);
'''


def digest(value):
    return sha256(encode(value)).hexdigest()


@contextmanager
def journal(config, *, initialize=False):
    root = Path(config['state_dir'])
    fd = os.open(root / 'broker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
             'unsafe broker lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            need(False, 'broker busy')
        path = root / 'broker.sqlite3'
        if initialize:
            os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        else:
            trusted_path(str(path), private=True)
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=EXTRA')
            target = encode(config['target']).decode('ascii')
            if initialize:
                with db:
                    db.executescript(SCHEMA)
                    db.execute('INSERT INTO identity VALUES (?)', (target,))
            need([row[0] for row in db.execute('SELECT target FROM identity')] == [target], 'journal target mismatch')
            yield Journal(db)
        finally:
            db.close()


class Journal:
    def __init__(self, db):
        self.db = db

    def latest(self, issue):
        return self.db.execute('SELECT * FROM runs WHERE issue=? ORDER BY id DESC LIMIT 1', (issue,)).fetchone()

    def reserve(self, data, authority, scope):
        issue, base, attempt = (data[name] for name in ('issue', 'base', 'attempt'))
        previous = self.db.execute('SELECT max(attempt) FROM runs WHERE issue=? AND base=?', (issue, base)).fetchone()[0]
        need(previous is None or attempt > previous, 'attempt has been superseded')
        with self.db:
            self.db.execute('INSERT INTO runs (issue,base,attempt,authority,scope) VALUES (?,?,?,?,?)',
                            (issue, base, attempt, authority, scope))
        return self.latest(issue)

    def action(self, run, stage):
        return self.db.execute('SELECT * FROM actions JOIN runs USING (id) WHERE id=? AND stage=?',
                               (run['id'], stage)).fetchone()

    def pending(self, stage, issue=None):
        args = (stage,) if issue is None else (stage, issue)
        return self.db.execute("SELECT * FROM actions JOIN runs USING (id) WHERE stage=? AND status='pending'"
                               + ('' if issue is None else ' AND issue=?'), args).fetchall()

    def start(self, run, stage, sha):
        if stage == 'deploy':
            need(self.db.execute("SELECT 1 FROM actions WHERE stage='deploy' AND sha=? AND status!='fenced'",
                                 (sha,)).fetchone() is None, 'deployment SHA already claimed')
        with self.db:
            self.db.execute('INSERT INTO actions VALUES (?,?,?,?)', (run['id'], stage, sha, 'pending'))
        return self.action(run, stage)

    def finish(self, action, status):
        with self.db:
            result = self.db.execute("UPDATE actions SET status=? WHERE id=? AND stage=? AND status='pending'",
                                     (status, action['id'], action['stage']))
            need(result.rowcount == 1, 'stage receipt is no longer pending')
