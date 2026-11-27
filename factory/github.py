"""Scoped GitHub reads and SHA-bound pull request operations."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import FactoryError, is_sha, require


PROJECT_ITEMS = '''
query($owner: String!, $number: Int!, $cursor: String) {
  organization(login: $owner) {
    login
    projectV2(number: $number) {
      title closed
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isArchived
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            __typename
            ... on Issue { number repository { nameWithOwner } }
          }
        }
      }
    }
  }
}
'''


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, config):
        self.config = config
        self.repo_path = '/repos/' + config.target['repo']
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.headers = {}
        for role, identity in (('repo', config.repo_identity), ('project', config.project_identity)):
            headers = {'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                       'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SilentDeluge-factory'}
            if identity.token_env:
                token = os.environ.get(identity.token_env, '')
                require(token and all(33 <= ord(ch) <= 126 for ch in token),
                        f'{role} identity token is missing or invalid')
                headers['Authorization'] = 'Bearer ' + token
            self.headers[role] = (identity.endpoint, headers)
        repo_auth = self.headers['repo'][1].get('Authorization')
        require(repo_auth is None or repo_auth != self.headers['project'][1].get('Authorization'),
                'repository and project tokens must differ')

    def request(self, role, operation, path, *, method='GET', data=None, missing=False):
        endpoint, headers = self.headers[role]
        if role == 'repo' and self.config.repo_identity.anonymous:
            require(method == 'GET' and data is None and path.startswith(self.repo_path + '/'),
                    'anonymous repository identity only permits repository reads')
        request = Request(endpoint + path, data=None if data is None else json.dumps(data).encode(),
                          headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                limit = 16 * 1024 * 1024
                body = response.read(limit + 1)
                require(len(body) <= limit, f'GitHub {operation} response too large')
                return json.loads(body)
        except HTTPError as error:
            code = error.code
            error.close()
            if missing and code == 404:
                return None
            raise FactoryError(f'GitHub {operation} HTTP {code}') from None
        except (URLError, OSError, ValueError) as error:
            raise FactoryError(f'GitHub {operation} response unavailable') from None

    def repo(self, operation, path, **kwargs):
        return self.request('repo', operation, self.repo_path + path, **kwargs)

    def pages(self, operation, path, **query):
        for page in range(1, 1001):
            batch = self.repo(operation, path + '?' + urlencode({**query, 'per_page': 100, 'page': page}))
            require(isinstance(batch, list) and len(batch) <= 100, f'invalid GitHub {operation} page')
            yield from batch
            if len(batch) < 100:
                return
        raise FactoryError(f'GitHub {operation} page limit exceeded')

    def open_issues(self):
        issues = {}
        for issue in self.pages('issue intake', '/issues', state='open', sort='created', direction='asc'):
            if 'pull_request' in issue:
                continue
            number = issue['number']
            require(type(number) is int and 0 < number < 2**31 and issue['state'] == 'open'
                    and number not in issues, 'invalid or duplicate GitHub issue; retry snapshot')
            labels = {label['name'].casefold() for label in issue['labels']}
            issues[number] = next((kind for kind in ('security', 'bug', 'enhancement', 'documentation')
                                   if kind in labels), 'unclassified')
        return issues

    def cards(self):
        target = self.config.target
        variables = {'owner': target['project_owner'], 'number': target['project_number'], 'cursor': None}
        cards = {}
        seen = set()
        while True:
            response = self.request('project', 'project intake', '/graphql', method='POST',
                                    data={'query': PROJECT_ITEMS, 'variables': variables})
            require(not response.get('errors'), 'GitHub project query rejected')
            org = response['data']['organization']
            project = org['projectV2'] if org else None
            require(project and org['login'].casefold() == target['project_owner'].casefold()
                    and project['title'] == target['project_title'] and project['closed'] is False,
                    'GitHub project missing, closed, or does not match configuration')
            items = project['items']
            for item in items['nodes']:
                content = item['content']
                if (item['isArchived'] or not content or content['__typename'] != 'Issue'
                        or content['repository']['nameWithOwner'].casefold() != target['repo'].casefold()):
                    continue
                number = content['number']
                status = item['fieldValueByName']
                name = status['name'] if status is not None else None
                require(type(number) is int and 0 < number < 2**31
                        and (name is None or isinstance(name, str) and len(name) <= 256),
                        'invalid GitHub project card')
                cards.setdefault(number, []).append(name)
            info = items['pageInfo']
            require(type(info['hasNextPage']) is bool, 'invalid GitHub project page')
            if not info['hasNextPage']:
                return cards
            cursor = info['endCursor']
            require(isinstance(cursor, str) and cursor and cursor not in seen and len(seen) < 1000,
                    'invalid GitHub project pagination')
            seen.add(cursor)
            variables['cursor'] = cursor

    def ref(self, branch, *, missing=False):
        ref = self.repo('branch read', '/git/ref/heads/' + quote(branch, safe='/'), missing=missing)
        if ref is None and missing:
            return None
        require(ref['ref'] == 'refs/heads/' + branch and ref['object']['type'] == 'commit'
                and is_sha(ref['object']['sha']), 'invalid GitHub branch response')
        return ref['object']['sha']

    def check_candidate(self, base, sha, approval):
        commit = self.repo('candidate read', f'/commits/{sha}?per_page=100&page=1')
        require(commit['sha'] == sha and [p['sha'] for p in commit['parents']] == [base],
                'candidate must be one commit directly atop the approved base')
        files = commit['files']
        require(isinstance(files, list) and 0 < len(files) < 100,
                'candidate change list missing, empty, or too large to certify')
        for file in files:
            require(approval.permits(file['filename']) and
                    (file['status'] != 'renamed' or approval.permits(file.get('previous_filename'))) and
                    ('previous_filename' not in file or approval.permits(file['previous_filename'])),
                    'candidate changes files outside approved scope')

    def pull_for(self, branch):
        owner = self.config.target['repo'].split('/')[0]
        pulls = list(self.pages('pull request lookup', '/pulls', state='all', head=f'{owner}:{branch}'))
        require(len(pulls) <= 1, 'multiple pull requests claim the factory branch')
        if not pulls:
            return None
        return self.pull(pulls[0]['number'])

    def pull(self, number):
        require(type(number) is int and 0 < number < 2**31, 'invalid GitHub pull request number')
        pull = self.repo('pull request read', f'/pulls/{number}')
        require(pull['number'] == number, 'GitHub pull request number mismatch')
        return pull

    def open_pull(self, issue, branch):
        self.repo('pull request creation', '/pulls', method='POST', data={
            'title': f'Factory implementation for issue #{issue}', 'head': branch,
            'base': self.config.target['base_ref'], 'body': f'Closes #{issue}\n', 'maintainer_can_modify': False,
        })

    def check_pull(self, pull, branch, sha):
        repo = self.config.target['repo'].casefold()
        require(all(pull[side]['repo'] and pull[side]['repo']['full_name'].casefold() == repo
                    for side in ('head', 'base'))
                and pull['base']['ref'] == self.config.target['base_ref']
                and pull['head']['ref'] == branch and pull['head']['sha'] == sha,
                'pull request does not match the verified repository, refs, and SHA')

    def merge(self, pull, issue, sha):
        require(pull['state'] == 'open' and pull['draft'] is False, 'pull request is closed or draft')
        response = self.repo('pull request merge', f"/pulls/{pull['number']}/merge", method='PUT',
                             data={'sha': sha, 'merge_method': 'merge',
                                   'commit_title': f'Factory implementation for issue #{issue}',
                                   'commit_message': f'Closes #{issue}'})
        require(response['merged'] is True, 'GitHub refused the merge')

    def merged_sha(self, pull, base, sha):
        merged_sha = pull['merge_commit_sha']
        require(pull['merged'] is True and is_sha(merged_sha), 'GitHub has not confirmed a merge commit')
        commit = self.repo('merge commit read', f'/git/commits/{merged_sha}')
        require(commit['sha'] == merged_sha and [p['sha'] for p in commit['parents']] == [base, sha],
                'merge commit parents do not match the verified base and candidate')
        return merged_sha
