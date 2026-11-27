"""Intake and policy-controlled promotion; remote prose never reaches a stage command."""

import json
import subprocess
import tempfile

from .config import ASTRA, SOL, STAGES, FactoryError, is_sha, require, serial
from .state import describe


DONE = {'implement': 'implemented', 'verify': 'verified', 'publish': 'published',
        'merge': 'merged', 'deploy': 'deployed'}
INVALID_UPSTREAM = 'invalid upstream response; inspect the configured GitHub or broker service'


def phase_order(phase):
    if phase == 'reserved':
        return 0
    for index, stage in enumerate(STAGES):
        if phase == DONE[stage]:
            return 2 * index + 2
        if phase in (f'{stage}-running', f'{stage}-failed'):
            return 2 * index + 1
    raise FactoryError('unknown journal phase')


def broker_call(policy, stage, payload):
    # The broker is trusted infrastructure; never execute anything from the candidate checkout.
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([str(policy.broker), stage], input=serial(payload).encode(),
                                    stdout=output, stderr=subprocess.DEVNULL, cwd='/',
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=3600)
            require(result.returncode == 0, f'{stage} broker failed (exit {result.returncode})')
            output.seek(0)
            body = output.read(4097)
            require(len(body) <= 4096, f'{stage} broker response too large')
            return json.loads(body)
    except subprocess.TimeoutExpired:
        raise FactoryError(f'{stage} broker timed out; recover using the same journal entry') from None
    except (ValueError, OSError):
        raise FactoryError(f'{stage} broker response unavailable or invalid') from None


def require_promotion(config, policy):
    require(policy is not None, 'promotion requires an explicit operator policy')
    require(not config.repo_identity.anonymous, 'promotion requires a non-anonymous repository identity')


class Pipeline:
    def __init__(self, config, state, github, policy):
        self.config = config
        self.state = state
        self.gh = github
        self.policy = policy
        self.base_ref = config.target['base_ref']

    def snapshot(self):
        issues = self.gh.open_issues()
        cards = self.gh.cards()
        base = self.gh.ref(self.base_ref)
        approval_keys = self.policy.approvals if self.policy is not None else ()
        self.state.observe(self.config.target['repo'], base, issues, cards, approval_keys=approval_keys)
        return issues, cards, base

    def status(self, issue=None):
        intake = self.state.metadata('intake')
        base = intake['base'] if intake else None
        packets = self.state.investigation_packets(issue=issue, base=base) if intake else []
        current_packets = {packet['issue']: packet for packet in packets if packet['base'] == base}
        entries = []
        for entry in self.state.observations():
            number = entry['issue']
            if issue is not None and number != issue:
                continue
            statuses = entry['card_statuses']
            attempt = self.state.latest(number)
            approval = self.policy.approvals.get((number, base)) if self.policy else None
            if not entry['open']:
                disposition = 'not-open-in-last-intake'
            elif not statuses:
                disposition = 'investigate-missing-project-card'
            elif len(statuses) != 1:
                disposition = 'investigate-duplicate-project-cards'
            elif statuses[0] != self.config.ready_status:
                disposition = 'await-project-readiness'
            elif attempt:
                disposition = 'attempt-recorded' if attempt['base'] == base else 'attempt-on-prior-base'
            elif approval:
                disposition = 'dispatchable'
            else:
                disposition = 'await-maintainer-approval'
            entries.append({**entry, 'disposition': disposition, 'latest_attempt': describe(attempt),
                            'investigation_packet': current_packets.get(number)})
        result = {'target': self.config.target, 'last_intake': intake, 'issues': entries}
        if issue is not None:
            result['latest_attempt'] = describe(self.state.latest(issue))
            result['history'] = self.state.history(issue)
            result['investigation_packets'] = packets
        return result

    def reconcile(self):
        require_promotion(self.config, self.policy)
        observation = self.snapshot()
        granted_issues = {issue for issue, _ in self.policy.approvals}
        outcomes = []
        refresh = False
        for issue in sorted(observation[0].keys() | granted_issues):
            outcome = {'issue': issue, 'base': observation[2], 'stages': [], 'attempt': None}
            try:
                has_grant = issue in granted_issues
                if refresh or (has_grant and self.gh.ref(self.base_ref) != observation[2]):
                    observation = self.snapshot()
                    refresh = False
                outcome['base'] = observation[2]
                self._reconcile_issue(issue, observation, has_grant, outcome)
            except (FactoryError, KeyError, TypeError, AttributeError, ValueError) as error:
                message = str(error) if isinstance(error, FactoryError) else INVALID_UPSTREAM
                outcome.update(result='failed', stage=outcome.get('stage'), error=message,
                               attempt=describe(self.state.latest(issue)))
            # A merge could have changed both the base and open-issue list even if its receipt was lost.
            refresh = (refresh or outcome.get('stage') == 'merge'
                       or any(step['stage'] == 'merge' for step in outcome['stages']))
            outcomes.append(outcome)
        return {'target': self.config.target, 'issues': outcomes}

    def _reconcile_issue(self, issue, observation, has_grant, outcome):
        issues, cards, base = observation
        run = self.state.latest(issue)
        outcome['attempt'] = describe(run)
        phase = run['phase'] if run is not None else 'reserved'
        if phase == DONE['deploy']:
            outcome.update(result='duplicate', base=run['base'])
            return

        # These phases may no longer have an open issue or their original base at the tip.
        # Only the recorded attempt's grant permits recovery; run_stage still checks the PR and SHA.
        recovering = phase in ('merge-running', 'merge-failed', 'merged', 'deploy-running', 'deploy-failed')
        if recovering:
            base = outcome['base'] = run['base']
        approval = self.policy.approvals.get((issue, base))
        if not recovering and issue not in issues:
            reason = 'not-open'
        elif not recovering and cards.get(issue) != [self.config.ready_status]:
            reason = 'not-ready'
        elif approval is None:
            reason = 'wrong-base' if has_grant else 'no-grant'
        elif run is not None and run['base'] != base:
            require(phase_order(phase) > phase_order(DONE['verify']),
                    'prior unpublished attempt is on a stale base; fence outstanding work before explicit retry')
            reason = 'wrong-base'
        else:
            completed = phase_order(phase) // 2
            for stage in approval.stages[completed:]:
                outcome['stage'] = stage
                result = self.run_stage(issue, stage, expected_base=base)
                outcome['stages'].append({'stage': stage, 'result': result['result']})
                outcome['attempt'] = result['attempt']
                del outcome['stage']
            outcome['result'] = 'advanced' if outcome['stages'] else 'duplicate'
            return
        outcome.update(result='skipped', reason=reason)

    def eligible(self, issue, base, issues, cards):
        require(issue in issues, 'issue is not open in the configured repository')
        require(cards.get(issue) == [self.config.ready_status],
                'issue must have exactly one active project card in the configured ready status')
        return self.approved(issue, base, 'implement')

    def approved(self, issue, base, stage):
        require_promotion(self.config, self.policy)
        approval = self.policy.approvals.get((issue, base))
        require(approval is not None and stage in approval.stages,
                f'no scoped maintainer grant for {stage} at this issue and base')
        return approval

    def retry(self, issue):
        require_promotion(self.config, self.policy)
        previous = self.state.latest(issue)
        require(previous is not None, 'no attempt to retry; use dispatch')
        issues, cards, base = self.snapshot()
        approval = self.eligible(issue, base, issues, cards)
        failed = previous['phase'] in ('implement-failed', 'verify-failed')
        stale = previous['base'] != base and previous['phase'] in ('reserved', 'implemented', 'verified')
        require(failed or stale, 'cannot replace active or published attempt; resume its stage')
        require(self.gh.pull_for(previous['branch']) is None, 'cannot replace an attempt with a pull request')
        return {'result': 'retry-reserved', 'attempt': describe(self.state.reserve(issue, base, approval.fingerprint))}

    def run_stage(self, issue, stage, *, expected_base=None):
        require_promotion(self.config, self.policy)
        run = self.state.latest(issue)
        index = STAGES.index(stage)
        if run is None:
            require(stage == 'implement', 'no attempt; dispatch first')
            issues, cards, base = self.snapshot()
            require(expected_base is None or base == expected_base, 'approved base changed before reservation')
            approval = self.eligible(issue, base, issues, cards)
            run = self.state.reserve(issue, base, approval.fingerprint)
        elif phase_order(run['phase']) >= phase_order(DONE[stage]):
            return {'result': 'duplicate', 'attempt': describe(run)}
        require(expected_base is None or run['base'] == expected_base, 'attempt does not match the scheduled base')
        require(phase_order(run['phase']) >= 2 * index, 'previous stage has not passed')
        approval = self.approved(issue, run['base'], stage)
        require(run['policy'] == approval.fingerprint, 'approved task, scope, tests, or broker changed since reservation')
        resuming = run['phase'] in (f'{stage}-running', f'{stage}-failed')
        if stage == 'implement' and not resuming:
            require(self.gh.ref(run['branch'], missing=True) is None and self.gh.pull_for(run['branch']) is None,
                    'unclaimed factory branch or pull request already exists')

        # Record the intent before any remote write or broker execution. Recovery reuses its identity.
        self.state.transition(run, f'{stage}-running')
        try:
            fields = getattr(self, stage)(run, approval)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            self.state.transition(run, f'{stage}-failed', error=INVALID_UPSTREAM)
            raise FactoryError(INVALID_UPSTREAM) from error
        except FactoryError as error:
            self.state.transition(run, f'{stage}-failed', error=str(error))
            raise
        result = self.state.transition(run, DONE[stage], **fields)
        return {'result': 'resumed' if resuming else 'advanced', 'attempt': describe(result)}

    def require_current(self, run):
        issues, cards, base = self.snapshot()
        require(base == run['base'], 'approved base is no longer current')
        self.eligible(run['issue'], base, issues, cards)

    def payload(self, run):
        return {'target': self.config.target, **{k: run[k] for k in ('issue', 'base', 'attempt', 'branch')}}

    def candidate(self, run, approval, sha):
        require(self.gh.ref(run['branch']) == sha, 'candidate branch no longer matches its attested SHA')
        self.gh.check_candidate(run['base'], sha, approval)

    def implement(self, run, approval):
        self.require_current(run)
        response = broker_call(self.policy, 'implement', {**self.payload(run), 'model': SOL,
                               'task': approval.task, 'paths': approval.paths})
        require(isinstance(response, dict) and set(response) == {'model', 'sha'}
                and response['model'] == SOL and is_sha(response['sha']), 'invalid Sol implementation receipt')
        sha = response['sha']
        self.candidate(run, approval, sha)
        return {'sha': sha}

    def verify(self, run, approval):
        self.require_current(run)
        sha = run['sha']
        self.candidate(run, approval, sha)
        response = broker_call(self.policy, 'verify', {**self.payload(run), 'model': ASTRA, 'sha': sha,
                               'task': approval.task, 'paths': approval.paths, 'tests': approval.tests})
        require(response == {'model': ASTRA, 'sha': sha, 'tests': 'passed', 'review': 'passed'},
                'verification requires passing tests and Astra review for the exact candidate SHA')
        require(self.gh.ref(run['branch']) == sha, 'candidate branch moved during verification')
        return {}

    def publish(self, run, approval):
        self.require_current(run)
        self.candidate(run, approval, run['sha'])
        pull = self.gh.pull_for(run['branch'])
        if pull is None:
            self.state.begin_pr_creation(run)
            self.gh.open_pull(run['issue'], run['branch'])
            pull = self.gh.pull_for(run['branch'])
        else:
            require(run['pr_create_started'], 'unexpected pull request exists; investigate before promotion')
        require(pull is not None, 'GitHub has not confirmed the pull request')
        self.gh.check_pull(pull, run['branch'], run['sha'])
        require(pull['state'] == 'open' and pull['merged'] is False, 'pull request is already closed or merged')
        return {'pr': pull['number']}

    def known_pull(self, run):
        pull = self.gh.pull(run['pr'])
        self.gh.check_pull(pull, run['branch'], run['sha'])
        return pull

    def merge(self, run, approval):
        pull = self.known_pull(run)
        if pull['merged'] is not True:
            self.require_current(run)
            self.candidate(run, approval, run['sha'])
            require(self.gh.ref(self.base_ref) == run['base'], 'base moved before merge')
            self.gh.merge(pull, run['issue'], run['sha'])
            pull = self.known_pull(run)
        return {'merged_sha': self.gh.merged_sha(pull, run['base'], run['sha'])}

    def deploy(self, run, approval):
        merged = self.gh.merged_sha(self.known_pull(run), run['base'], run['sha'])
        require(merged == run['merged_sha'] and self.gh.ref(self.base_ref) == merged,
                'verified merge is not the current configured deployment ref')
        payload = self.payload(run)
        del payload['branch']
        response = broker_call(self.policy, 'deploy', {**payload, 'sha': merged})
        require(response == {'sha': merged, 'deployed': True}, 'broker did not confirm deployment of the merged SHA')
        return {}
