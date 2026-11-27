"""Synthetic subprocess/authority integration; nothing runs from the candidate tree."""

from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOL = 'openai-codex/gpt-6.2-sol'
ASTRA = 'openai-codex/gpt-6.2-astra'
TARGET = {'repo': 'example-owner/example-repo', 'project_owner': 'example-owner', 'project_number': 1,
          'project_title': 'Inventory Foundations', 'base_ref': 'main'}
TASK = 'Correct the existing example.'
TESTS = ['/opt/factory-tests/check', '--synthetic']
START = 'old example\n'
CHANGE = 'new example\n'

BOOTSTRAP = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from factory_broker.__main__ import serve
sys.exit(serve(sys.argv[3:], sys.stdin.buffer, sys.stdout.buffer, sys.stderr, Path(sys.argv[2])))
'''

ADAPTER = r'''#!/usr/bin/python3 -IB
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).parent
remote = root / 'public.git'
control = json.loads((root / 'control').read_text())
role, verb, *args = Path(__file__).name, *sys.argv[1:]
assert not any(name in os.environ for name in (
    'FACTORY_REPO_TOKEN', 'FACTORY_PROJECT_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK', 'GNUPGHOME', 'OPENAI_API_KEY'))
assert not any(Path.cwd().iterdir()) and not any(Path(os.environ['HOME']).iterdir())
data = json.load(sys.stdin) if role != 'runner' else None
with (root / 'calls').open('a') as out:
    out.write(json.dumps([role, verb, args, data]) + '\n')

if role == 'model':
    assert verb == 'run' and args[:2] == ['--', '/opt/omp/omp']
    flags = {'--no-tools', '--no-context-files', '--no-extensions', '--no-session'}
    assert flags <= set(args) and args[-1] == '@/run/factory-model/prompt.json'
    model = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--model='))
    if 'files' in data:
        assert model == 'openai-codex/gpt-6.2-sol'
        assert data['files'] == {'README.md': 'old example\n'}
        text = {'replacements': control.get('edits', {'README.md': 'new example\n'})}
    else:
        assert model == 'openai-codex/gpt-6.2-astra'
        assert data['changes'] == {'README.md': {'before': 'old example\n', 'after': 'new example\n'}}
        text = {'review': 'passed'}
    print(json.dumps({'model': control.get('model', model), 'output': json.dumps(text)}))

elif role == 'runner':
    assert verb == '--workspace' and args[1:] == ['--timeout', '10', '--', '/opt/factory-tests/check', '--synthetic']
    tree = Path(args[0])
    assert (tree / 'README.md').read_text() == 'new example\n'
    assert (tree / 'candidate.py').is_file() and not (tree / '.git').exists()
    print('{"tests":"passed","secret":"NEVER-FORWARD"}')
    sys.exit(1 if control.get('fail_test') else 0)

else:
    assert role in ('publisher', 'deployer')
    assert data['target']['repo'] == 'example-owner/example-repo'
    record = root / (data['key'] + '.receipt')
    if verb == 'observe':
        if not record.is_file() or control.get('hide_receipt'):
            sys.exit(1)
        print(record.read_text())
    else:
        assert not record.exists()
        expected = f"refs/heads/silentdeluge-factory/i{data['issue']}-{data['base']}/a{data['attempt']}"
        if role == 'publisher':
            assert verb == 'publish' and data['ref'] == expected
            if control.get('unknown_publish'):
                sys.exit(1)
            def git(*argv, body=None):
                subprocess.run(['/usr/bin/git', f'--git-dir={remote}', '-c', 'core.hooksPath=/dev/null', *argv],
                               input=body, check=True, stdout=subprocess.DEVNULL)
            git('unpack-objects', '-q', body=base64.b64decode(data['pack'], validate=True))
            git('update-ref', expected, data['sha'], '0' * 40)
            status = 'published'
        else:
            assert verb == 'deploy' and 'ref' not in data and 'pack' not in data
            status = 'deployed'
        record.write_text(json.dumps({'key': data['key'], 'sha': data['sha'], 'status': status}))
        if control.get('lost_' + verb):
            sys.exit(1)
'''


class FactoryBrokerIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='test-factory-broker-', dir='/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / 'public.git'
        self.config_path = self.root / 'broker.json'
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.git_env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1',
                        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_AUTHOR_NAME': 'Fixture',
                        'GIT_COMMITTER_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@invalid',
                        'GIT_COMMITTER_EMAIL': 'fixture@invalid'}
        self.git('init', '--quiet', '--bare', '--template=')
        self.files = {
            'README.md': ('100644', START.encode()), 'private.txt': ('100644', b'out of scope\n'),
            '.gitattributes': ('100644', b'README.md export-ignore\n'),
            'candidate.py': ('100755', f"open({str(self.root / 'executed')!r}, 'w').close()\n".encode()),
        }
        self.base = self.commit(self.make_tree(self.files))
        self.git('update-ref', 'refs/heads/main', self.base)
        for name in ('model', 'runner', 'publisher', 'deployer'):
            adapter = self.root / name
            adapter.write_text(ADAPTER)
            adapter.chmod(0o700)
        self.control()
        self.config = {
            'target': TARGET, 'models': {'implement': SOL, 'verify': ASTRA}, 'revision': 'fixture-1',
            'state_dir': str(self.state), 'public_mirror': str(self.remote), 'omp': '/opt/omp/omp',
            'model_runner': str(self.root / 'model'), 'publisher': str(self.root / 'publisher'),
            'workspace_runner': str(self.root / 'runner'), 'tests': TESTS, 'test_timeout': 10,
            'deployer': str(self.root / 'deployer'),
            'approvals': [{'issue': 42, 'base': self.base, 'task': TASK, 'paths': ['README.md'],
                           'stages': ['implement', 'verify', 'deploy']}],
        }
        self.save_config()
        initialized = self.invoke('--initialize')
        self.assertEqual(initialized.returncode, 0, initialized.stderr.decode())

    def control(self, **values):
        (self.root / 'control').write_text(json.dumps(values))

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def git(self, *argv, body=None):
        return subprocess.run(['/usr/bin/git', f'--git-dir={self.remote}', '-c', 'core.hooksPath=/dev/null', *argv],
                              input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, cwd=self.root,
                              env=self.git_env, umask=0o077).stdout.rstrip(b'\n').decode('utf-8')

    def make_tree(self, files):
        lines = []
        for path, (mode, body) in files.items():
            oid = self.git('hash-object', '-w', '--stdin', body=body)
            lines.append(f'{mode} blob {oid}\t{path}\n')
        return self.git('mktree', body=''.join(lines).encode())

    def commit(self, tree, *parents):
        return self.git('commit-tree', tree, *(word for p in parents for word in ('-p', p)), body=b'fixture\n')

    def branch(self, attempt=1):
        return f'silentdeluge-factory/i42-{self.base}/a{attempt}'

    def payload(self, stage, attempt=1, **overrides):
        data = {'target': TARGET, 'issue': 42, 'base': self.base, 'attempt': attempt}
        if stage != 'deploy':
            data.update(branch=self.branch(attempt), model=SOL if stage == 'implement' else ASTRA,
                        task=TASK, paths=['README.md'])
        if stage == 'verify':
            data['tests'] = TESTS
        return {**data, **overrides}

    def invoke(self, stage, data=None):
        env = dict(self.git_env, SSH_AUTH_SOCK='/tmp/never-forward', GNUPGHOME='/tmp/never-forward',
                   FACTORY_REPO_TOKEN='NEVER-FORWARD', FACTORY_PROJECT_TOKEN='NEVER-FORWARD',
                   GITHUB_TOKEN='NEVER-FORWARD', OPENAI_API_KEY='NEVER-FORWARD')
        result = subprocess.run([sys.executable, '-IB', '-c', BOOTSTRAP, str(Path(__file__).resolve().parent),
                                 str(self.config_path), stage], input=json.dumps(data).encode(),
                                capture_output=True, cwd='/', env=env, timeout=30)
        self.assertNotIn(b'NEVER-FORWARD', result.stdout + result.stderr)
        self.assertFalse((self.root / 'executed').exists())
        return result

    def call(self, stage, attempt=1, *, success=True, **overrides):
        result = self.invoke(stage, self.payload(stage, attempt, **overrides))
        if success:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertEqual(result.stdout, b'')
        return result.stderr

    def calls(self, role, verb=None):
        path = self.root / 'calls'
        return [entry for line in path.read_text().splitlines() if (entry := json.loads(line))[0] == role
                and (verb is None or entry[1] == verb)] if path.exists() else []

    def side_refs(self):
        return self.git('for-each-ref', '--format=%(refname)', 'refs/heads/silentdeluge-factory')

    def test_exact_candidate_review_and_rollout_are_replayed_without_reexecution(self):
        receipt = self.call('implement')
        self.assertEqual(receipt['model'], SOL)
        candidate = receipt['sha']
        self.assertEqual(self.git('rev-parse', self.branch()), candidate)
        self.assertEqual(self.git('rev-parse', candidate + '^'), self.base)
        self.assertEqual(self.git('show', candidate + ':README.md'), 'new example')
        self.assertEqual(self.call('implement'), receipt)
        self.assertEqual(len(self.calls('publisher', 'publish')), 1)
        verify = {'sha': candidate, 'model': ASTRA, 'review': 'passed', 'tests': 'passed'}
        self.assertEqual(self.call('verify', sha=candidate), verify)
        self.assertEqual(self.call('verify', sha=candidate), verify)
        self.assertEqual(len(self.calls('runner')), 1)
        self.assertEqual(len(self.calls('model')), 2)
        self.assertEqual(self.calls('model')[1][3]['sha'], candidate)

        # Correct parents with a different tree do not deploy.
        wrong = self.commit(self.git('rev-parse', self.base + '^{tree}'), self.base, candidate)
        self.git('update-ref', 'refs/heads/main', wrong)
        self.call('deploy', sha=wrong, success=False)
        self.assertFalse(self.calls('deployer'))
        merge = self.commit(self.git('rev-parse', candidate + '^{tree}'), self.base, candidate)
        self.git('update-ref', 'refs/heads/main', merge)
        self.control(lost_deploy=True)
        self.call('deploy', sha=merge, success=False)
        self.assertEqual(self.call('deploy', sha=merge), {'sha': merge, 'deployed': True})
        self.assertEqual(self.call('deploy', sha=merge), {'sha': merge, 'deployed': True})
        self.assertEqual(len(self.calls('deployer', 'deploy')), 1)

    def test_model_replacements_cannot_escape_or_add_files(self):
        for path in ('../outside', '/etc/passwd', '.git/config', '.github/workflows/ci.yml', 'private.txt', 'new.py'):
            with self.subTest(path=path):
                self.control(edits={path: 'not permitted'})
                self.call('implement', success=False)
                self.assertFalse(self.calls('publisher'))
                self.assertEqual(self.side_refs(), '')
        self.assertEqual(self.git('rev-parse', 'main'), self.base)

    def test_curated_target_branch_and_model_cannot_be_overridden_or_claim_existing_ref(self):
        for bad_branch in ('main', 'refs/heads/main', self.branch() + '/../../main', self.branch().replace('i42', 'i43')):
            with self.subTest(branch=bad_branch):
                self.call('implement', branch=bad_branch, success=False)
        wrong_target = deepcopy(TARGET)
        wrong_target['repo'] = 'other/private'
        self.call('implement', target=wrong_target, success=False)
        self.call('implement', task='issue prose', success=False)
        self.call('implement', paths=['private.txt'], success=False)
        self.call('implement', model=ASTRA, success=False)
        self.assertFalse(self.calls('model'))
        self.git('update-ref', 'refs/heads/' + self.branch(), self.base)
        self.call('implement', success=False)
        self.assertFalse(self.calls('publisher'))
        self.assertEqual(self.git('rev-parse', self.branch()), self.base)
        self.assertEqual(self.git('rev-parse', 'main'), self.base)

    def test_unsafe_base_symlink_is_refused_before_prompt(self):
        self.base = self.commit(self.make_tree({**self.files, 'escape': ('120000', b'../../etc/shadow')}))
        self.git('update-ref', 'refs/heads/main', self.base)
        self.config['approvals'][0]['base'] = self.base
        self.save_config()
        self.call('implement', success=False)
        self.assertFalse(self.calls('model') or self.calls('publisher'))

    def test_public_ref_without_observed_receipt_cannot_recover_publication(self):
        self.control(lost_publish=True, hide_receipt=True)
        self.call('implement', success=False)
        candidate = self.git('rev-parse', self.branch())
        self.call('implement', success=False)
        self.call('implement', attempt=2, success=False)
        self.assertEqual(len(self.calls('publisher', 'publish')), 1)
        self.control()
        self.assertEqual(self.call('implement'), {'model': SOL, 'sha': candidate})
        self.assertEqual(len(self.calls('model')), 1)
        self.assertEqual(len(self.calls('publisher', 'publish')), 1)

    def test_absent_ref_does_not_fence_older_attempt_and_late_old_work_cannot_publish(self):
        self.control(unknown_publish=True)
        self.call('implement', success=False)
        self.assertEqual(self.side_refs(), '')
        self.call('implement', attempt=2, success=False)
        [[_, _, _, first]] = self.calls('publisher', 'publish')
        # An operator-controlled fixture supplies an explicit durable terminal fence.
        (self.root / (first['key'] + '.receipt')).write_text(json.dumps(
            {'key': first['key'], 'sha': first['sha'], 'status': 'fenced'}))
        self.control()
        candidate = self.call('implement', attempt=2)['sha']
        self.call('implement', success=False)
        self.assertEqual(self.side_refs(), 'refs/heads/' + self.branch(2))
        self.assertEqual(self.git('rev-parse', self.branch(2)), candidate)
        self.assertEqual(len(self.calls('publisher', 'publish')), 2)

    def test_verify_uses_fixed_runner_and_its_exit_not_text_claims(self):
        self.control(model=ASTRA)
        self.call('implement', success=False)
        self.assertFalse(self.calls('publisher'))
        self.control()
        candidate = self.call('implement')['sha']
        self.call('verify', sha=candidate, tests=['/bin/true'], success=False)
        self.git('update-ref', 'refs/heads/' + self.branch(), self.base)
        self.call('verify', sha=candidate, success=False)
        self.assertFalse(self.calls('runner'))
        self.git('update-ref', 'refs/heads/' + self.branch(), candidate)
        self.control(fail_test=True)
        self.call('verify', sha=candidate, success=False)
        self.control()
        self.call('verify', sha=candidate, success=False)
        self.assertEqual(len(self.calls('runner')), 1)
        self.assertEqual(len(self.calls('model')), 3)
        merge = self.commit(self.git('rev-parse', candidate + '^{tree}'), self.base, candidate)
        self.git('update-ref', 'refs/heads/main', merge)
        self.call('deploy', sha=merge, success=False)
        self.assertFalse(self.calls('deployer'))

    def test_busy_lock_and_missing_journal_fail_closed(self):
        with (self.state / 'broker.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertIn(b'broker busy', self.call('implement', success=False))
        (self.state / 'broker.sqlite3').rename(self.state / 'saved.sqlite3')
        self.call('implement', success=False)
        self.assertFalse(self.calls('model') or self.calls('publisher'))


if __name__ == '__main__':
    unittest.main()
