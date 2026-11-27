"""Trusted operator settings; GitHub content never enters this module."""

from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


SOL = 'openai-codex/gpt-6.2-sol'
ASTRA = 'openai-codex/gpt-6.2-astra'
STAGES = ('implement', 'verify', 'publish', 'merge', 'deploy')


class FactoryError(Exception):
    """A diagnostic safe to print without remote content or command output."""


def require(condition, message):
    if not condition:
        raise FactoryError(message)


def is_sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) is not None


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def trusted_file(path, private=False):
    info = path.lstat()
    allowed_bits = 0o077 if private else 0o022
    require(stat.S_ISREG(info.st_mode) and info.st_uid in {0, os.geteuid()}
            and not info.st_mode & allowed_bits, 'unsafe operator file permissions')


def read_settings(path, private=False):
    trusted_file(path, private)
    with path.open(encoding='utf-8') as file:
        return json.load(file)


def keys(obj, required, optional=()):
    require(isinstance(obj, dict) and set(required) <= obj.keys()
            and obj.keys() <= set(required) | set(optional), 'invalid settings fields')


def word(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


@dataclass(frozen=True)
class Identity:
    endpoint: str
    token_env: str | None
    anonymous: bool = False

    @classmethod
    def load(cls, data):
        keys(data, (), ('token_env', 'endpoint', 'anonymous'))
        name = data.get('token_env')
        require(name is None or word(name, '[A-Z_][A-Z0-9_]*'), 'invalid token reference')
        endpoint = data.get('endpoint', 'https://api.github.com')
        anonymous = data.get('anonymous', False)
        require(type(anonymous) is bool, 'invalid anonymous identity setting')
        if anonymous:
            require(endpoint == 'https://api.github.com' and name is None,
                    'anonymous identity requires api.github.com and no token')
            return cls(endpoint, None, anonymous=True)
        if endpoint == 'https://api.github.com':
            require(name is not None, 'GitHub identity requires a token reference')
        else:
            require(isinstance(endpoint, str), 'invalid local GitHub endpoint')
            url = urlsplit(endpoint)
            try:
                loopback = ipaddress.ip_address(url.hostname or '').is_loopback
                port = url.port
            except ValueError:
                loopback = False
            require(loopback and url.scheme == 'http' and port is not None
                    and url.username is None and url.password is None
                    and endpoint == f'http://{url.netloc}', 'invalid local GitHub endpoint')
        return cls(endpoint, name)


@dataclass(frozen=True)
class Config:
    target: dict
    ready_status: str
    repo_identity: Identity
    project_identity: Identity
    state_dir: Path

    @classmethod
    def load(cls, path):
        data = read_settings(path)
        keys(data, ('target', 'ready_status', 'repo_identity', 'project_identity', 'state_dir'))
        target = data['target']
        keys(target, ('repo', 'project_owner', 'project_number', 'project_title', 'base_ref'))
        require(word(target['repo'], '[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+')
                and target['repo'].split('/')[1] not in {'.', '..'}
                and word(target['project_owner'], '[a-zA-Z0-9-]+')
                and type(target['project_number']) is int and 0 < target['project_number'] < 2**31
                and word(target['base_ref'], '[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*')
                and target['base_ref'] != 'HEAD', 'invalid GitHub target')
        for text in (target['project_title'], data['ready_status']):
            require(isinstance(text, str) and 0 < len(text.strip()) <= 128, 'invalid project setting')
        repo = Identity.load(data['repo_identity'])
        project = Identity.load(data['project_identity'])
        require(not project.anonymous, 'project identity cannot be anonymous')
        require(repo != project and (repo.token_env is None or repo.token_env != project.token_env),
                'repository and project identities must be separate')
        require(isinstance(data['state_dir'], str), 'invalid state directory')
        state = Path(data['state_dir'])
        require(state.is_absolute(), 'state directory must be absolute')
        return cls(target, data['ready_status'], repo, project, state)


@dataclass(frozen=True)
class Approval:
    stages: tuple
    task: str
    paths: tuple
    tests: tuple
    fingerprint: str

    def permits(self, path):
        return (isinstance(path, str) and path.split('/')[0].casefold() != '.github'
                and all(part not in {'', '.', '..'} for part in path.split('/'))
                and not any(ord(ch) < 32 or ch == '\\' for ch in path)
                and any(path.startswith(scope) if scope.endswith('/') else path == scope
                        for scope in self.paths))


@dataclass(frozen=True)
class Policy:
    broker: Path
    approvals: dict

    @classmethod
    def load(cls, path, config):
        data = read_settings(path, private=True)
        keys(data, ('target', 'broker', 'broker_revision', 'models', 'tests', 'approvals'))
        require(data['target'] == config.target, 'policy target mismatch')
        require(data['models'] == {'implement': SOL, 'verify': ASTRA},
                'policy must pin exact Sol and Astra models')
        require(word(data['broker_revision'], '[a-zA-Z0-9._-]{1,128}'), 'invalid broker revision')
        require(isinstance(data['broker'], str), 'invalid stage broker')
        broker = Path(data['broker'])
        require(broker.is_absolute(), 'stage broker must be absolute')
        trusted_file(broker)
        require(os.access(broker, os.X_OK), 'stage broker is not executable')
        tests = data['tests']
        require(isinstance(tests, list) and tests and all(
            isinstance(arg, str) and arg and '\0' not in arg for arg in tests
        ) and tests[0].startswith('/'), 'verification requires a fixed absolute test command')
        require(isinstance(data['approvals'], list), 'invalid policy approvals')
        approvals = {}
        for item in data['approvals']:
            keys(item, ('issue', 'base', 'stages', 'task', 'paths'))
            number, base, stages, task, paths = (item[key] for key in
                                                ('issue', 'base', 'stages', 'task', 'paths'))
            require(type(number) is int and 0 < number < 2**31 and is_sha(base),
                    'invalid policy issue or base')
            require(isinstance(stages, list) and 0 < len(stages) <= len(STAGES)
                    and tuple(stages) == STAGES[:len(stages)], 'invalid policy stage grants')
            require(isinstance(task, str) and 0 < len(task.strip()) <= 16000,
                    'policy requires a trusted task specification')
            require(isinstance(paths, list) and paths and all(
                word(scope, '[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*/?')
                and not any(part in {'.', '..'} for part in scope.rstrip('/').split('/'))
                and scope.split('/')[0].casefold() != '.github' for scope in paths
            ), 'policy requires explicit non-workflow file scopes')
            require((number, base) not in approvals, 'duplicate policy approval')
            fingerprint = sha256(serial([str(broker), data['broker_revision'], tests, task, paths]).encode())
            approvals[number, base] = Approval(tuple(stages), task, tuple(paths), tuple(tests),
                                               fingerprint.hexdigest())
        return cls(broker, approvals)
