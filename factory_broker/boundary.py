"""Private policy and bounded, non-shell process I/O."""

import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import tempfile
import time


MODELS = {'implement': 'openai-codex/gpt-6.2-sol', 'verify': 'openai-codex/gpt-6.2-astra'}
STAGES = ('implement', 'verify', 'deploy')
MAX_REQUEST = 256 * 1024
MAX_MODEL = 512 * 1024
MAX_FILE = 128 * 1024


class Refused(Exception):
    """Only fixed, content-free diagnostics cross the broker boundary."""


def need(condition, message='invalid request or private configuration'):
    if not condition:
        raise Refused(message)


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def sha(value):
    return matches(value, '[0-9a-f]{40}') and value != '0' * 40


def number(value):
    return type(value) is int and 0 < value < 2**31


def fields(obj, names):
    need(isinstance(obj, dict) and set(obj) == set(names.split()))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def decode(raw):
    def unique(pairs):
        obj = dict(pairs)
        need(len(pairs) == len(obj), 'duplicate JSON field')
        return obj

    def invalid(_):
        raise Refused('invalid JSON constant')

    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)


def text_bytes(value):
    need(isinstance(value, str) and '\x00' not in value, 'not a UTF-8 text file')
    data = value.encode('utf-8')
    need(len(data) <= MAX_FILE, 'text file exceeds size limit')
    return data


def safe_path(value):
    return (matches(value, r'[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*') and len(value) <= 512
            and all(part.casefold() not in {'.', '..', '.git'} and len(part) <= 255
                    for part in value.split('/')))


def permitted(path, scopes):
    return (safe_path(path) and path.split('/')[0].casefold() != '.github'
            and any(path.startswith(scope) if scope.endswith('/') else path == scope for scope in scopes))


def trusted_path(value, *, directory=False, private=False, executable=False):
    need(isinstance(value, str))
    path = Path(value)
    need(path.is_absolute() and path.as_posix() == value and '..' not in path.parts)
    uid = os.geteuid()
    for parent in path.parents:
        info = parent.lstat()
        # Sticky /tmp permits isolated local fixtures; no other shared parent is accepted.
        sticky_tmp = parent == Path('/tmp') and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, uid}
             and (not info.st_mode & 0o022 or sticky_tmp), 'unsafe operator path')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    need(kind(info.st_mode) and info.st_uid in {0, uid} and not info.st_mode & (0o077 if private else 0o022),
         'unsafe operator path')
    if directory and private:
        need(info.st_uid == uid, 'state directory must belong to the broker user')
    if executable:
        need(os.access(path, os.X_OK), 'operator program is not executable')
    return path


def load(path):
    with trusted_path(str(path), private=True).open('rb') as stream:
        raw = stream.read(MAX_REQUEST + 1)
    need(len(raw) <= MAX_REQUEST)
    config = decode(raw)
    fields(config, 'target models revision state_dir public_mirror omp model_runner publisher workspace_runner '
                   'tests test_timeout deployer approvals')
    target = config['target']
    fields(target, 'repo project_owner project_number project_title base_ref')
    need(matches(target['repo'], '[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+')
         and target['repo'].split('/')[1] not in {'.', '..'}
         and matches(target['project_owner'], '[a-zA-Z0-9-]+') and number(target['project_number'])
         and isinstance(target['project_title'], str) and 0 < len(target['project_title'].strip()) <= 128
         and matches(target['base_ref'], r'[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*')
         and target['base_ref'] != 'HEAD' and target['base_ref'].split('/')[0] != 'silentdeluge-factory')
    need(config['models'] == MODELS and matches(config['revision'], r'[a-zA-Z0-9_.-]{1,128}'))
    trusted_path(config['state_dir'], directory=True, private=True)
    if config['public_mirror'] is not None:
        trusted_path(config['public_mirror'], directory=True)
    for name in ('model_runner', 'publisher', 'workspace_runner', 'deployer'):
        if name != 'deployer' or config[name] is not None:
            trusted_path(config[name], executable=True)
    # OMP and the test harness paths are absolute *inside* their respective guests.
    # Their image digests and provenance are enforced by the configured launchers.
    for item in (config['omp'], config['tests'][0] if isinstance(config['tests'], list) and config['tests'] else None):
        need(isinstance(item, str) and '\x00' not in item and Path(item).is_absolute())
    need(all(isinstance(arg, str) and arg and '\x00' not in arg for arg in config['tests'])
         and number(config['test_timeout']) and config['test_timeout'] <= 900)
    need(isinstance(config['approvals'], list))
    index = {}
    for grant in config['approvals']:
        fields(grant, 'issue base task paths stages')
        need(number(grant['issue']) and sha(grant['base']))
        task, paths, stages = grant['task'], grant['paths'], grant['stages']
        need(isinstance(task, str) and 0 < len(task.strip()) <= 16000)
        text_bytes(task)
        need(isinstance(paths, list) and paths and all(
            isinstance(path, str) and permitted(path.removesuffix('/'), [path.removesuffix('/')])
            for path in paths) and len(paths) == len(set(paths)))
        need(isinstance(stages, list) and stages and tuple(stages) == STAGES[:len(stages)]
             and ('deploy' not in stages or config['deployer'] is not None))
        key = grant['issue'], grant['base']
        need(key not in index)
        index[key] = grant
    return config, index


def request(stage, data, config, approvals):
    common = 'target issue base attempt'
    fields(data, common + {'implement': ' branch model task paths', 'verify': ' branch model task paths sha tests',
                           'deploy': ' sha'}[stage])
    need(encode(data['target']) == encode(config['target']) and number(data['issue'])
         and number(data['attempt']) and sha(data['base']))
    grant = approvals.get((data['issue'], data['base']))
    need(grant is not None and stage in grant['stages'], 'no broker stage grant')
    if stage != 'deploy':
        branch = f"silentdeluge-factory/i{data['issue']}-{data['base']}/a{data['attempt']}"
        need(data['branch'] == branch and data['model'] == MODELS[stage]
             and data['task'] == grant['task'] and data['paths'] == grant['paths'])
    if stage != 'implement':
        need(sha(data['sha']))
    if stage == 'verify':
        need(data['tests'] == config['tests'])
    return grant


class Commands:
    def __init__(self, root):
        self.cwd = root / 'empty'
        self.cwd.mkdir()
        home = root / 'home'
        home.mkdir()
        self.env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(home),
                    'XDG_CONFIG_HOME': str(home), 'XDG_CACHE_HOME': str(home), 'XDG_DATA_HOME': str(home)}

    def run(self, argv, body=b'', *, cap=0, timeout=60, extra_env=None, missing=None):
        # A regular stdin avoids pipe backpressure while stdout is read under a hard byte limit.
        with tempfile.TemporaryFile() as source:
            source.write(body)
            source.seek(0)
            deadline = time.monotonic() + timeout
            with subprocess.Popen(argv, cwd=self.cwd, env={**self.env, **(extra_env or {})},
                                  stdin=source, stdout=subprocess.PIPE if cap else subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True) as child:
                try:
                    output = bytearray()
                    if cap:
                        fd = child.stdout.fileno()
                        while True:
                            ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
                            need(ready and time.monotonic() < deadline, 'operator command timed out')
                            chunk = os.read(fd, min(65536, cap + 1 - len(output)))
                            if not chunk:
                                break
                            output.extend(chunk)
                            need(len(output) <= cap, 'operator response exceeds size limit')
                    code = child.wait(timeout=max(0, deadline - time.monotonic()))
                    if code == missing:
                        return None
                    need(code == 0, 'operator command failed')
                    return bytes(output)
                except BaseException:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    raise
