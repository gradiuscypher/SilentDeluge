"""Issue the controller's narrowly scoped GitHub App installation token."""

import base64
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener


_API = 'https://api.github.com'
_ENVIRONMENT = Path('/run/silentdeluge-factory-credentials/project.env')
_ORG = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
_TOKEN = re.compile(r'[A-Za-z0-9._-]{1,4096}')
_EXPIRY = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])'
)


class IssuerError(Exception):
    """A fixed diagnostic safe for the service journal."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _sign_rs256(message, key_pem):
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        raise IssuerError('system Python cryptography is unavailable') from None

    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise IssuerError('GitHub App credential must be an RSA private key')
        return key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    except IssuerError:
        raise
    except Exception:
        raise IssuerError('cannot sign with the GitHub App credential') from None


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


def _app_jwt(app_id, key_pem):
    now = int(datetime.now(timezone.utc).timestamp())
    claims = json.dumps({'iss': str(app_id), 'iat': now - 60, 'exp': now + 540},
                        separators=(',', ':')).encode('ascii')
    message = _base64url(b'{"alg":"RS256","typ":"JWT"}') + b'.' + _base64url(claims)
    return (message + b'.' + _base64url(_sign_rs256(message, key_pem))).decode('ascii')


def _request(opener, jwt, path, *, data=None):
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {jwt}',
        'User-Agent': 'silentdeluge-factory-project-refresh',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    status = 200
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode('ascii')
        status = 201
    request = Request(_API + path, headers=headers, data=data)
    try:
        with opener.open(request, timeout=20) as response:
            if response.status != status:
                raise IssuerError('unexpected GitHub App API response status')
            limit = 1024 * 1024
            body = response.read(limit + 1)
            if len(body) > limit:
                raise IssuerError('GitHub App API response too large')
            result = json.loads(body)
    except HTTPError as error:
        error.close()
        raise IssuerError('GitHub App API request failed') from None
    except (HTTPException, OSError, ValueError):
        raise IssuerError('GitHub App API request failed') from None
    if not isinstance(result, dict):
        raise IssuerError('invalid GitHub App API response')
    return result


def _require_token(token):
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise IssuerError('GitHub installation token is not safe for the environment file')


def mint_token(app_id, org, key_pem):
    """Verify the configured org's installation and return its read-only token."""
    if isinstance(app_id, str) and re.fullmatch(r'[0-9]{1,20}', app_id):
        app_id = int(app_id)
    if type(app_id) is not int or app_id <= 0:
        raise IssuerError('FACTORY_APP_ID must be a positive App ID')
    if not isinstance(org, str) or not _ORG.fullmatch(org):
        raise IssuerError('FACTORY_PROJECT_ORG must be a GitHub organization login')

    jwt = _app_jwt(app_id, key_pem)
    try:
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()),
                              _NoRedirect())
    except (OSError, ValueError):
        raise IssuerError('cannot initialize verified GitHub HTTPS') from None
    installation = _request(opener, jwt, f'/orgs/{org}/installation')
    installation_id = installation.get('id')
    installation_app = installation.get('app_id')
    account = installation.get('account')
    login = account.get('login') if isinstance(account, dict) else None
    if (type(installation_id) is not int or installation_id <= 0
            or type(installation_app) is not int or installation_app != app_id
            or not isinstance(login, str) or not _ORG.fullmatch(login)
            or login.lower() != org.lower() or account.get('type') != 'Organization'
            or installation.get('target_type') != 'Organization'
            or 'suspended_at' not in installation or installation['suspended_at'] is not None):
        raise IssuerError('GitHub installation does not match the active App and organization')

    requested = {'organization_projects': 'read'}
    result = _request(opener, jwt, f'/app/installations/{installation_id}/access_tokens',
                      data={'permissions': requested})
    if result.get('permissions') not in (requested, {**requested, 'metadata': 'read'}):
        raise IssuerError('GitHub installation token permissions do not match Projects read')

    timestamp = result.get('expires_at')
    if not isinstance(timestamp, str) or not _EXPIRY.fullmatch(timestamp):
        raise IssuerError('invalid GitHub installation token expiry')
    if timestamp.endswith('Z'):
        timestamp = timestamp[:-1] + '+00:00'
    try:
        lifetime = datetime.fromisoformat(timestamp) - datetime.now(timezone.utc)
    except ValueError:
        raise IssuerError('invalid GitHub installation token expiry') from None
    # GitHub issues one-hour tokens; allow a minute for clock skew.
    if not timedelta(0) < lifetime <= timedelta(hours=1, minutes=1):
        raise IssuerError('GitHub installation token is expired or not short-lived')

    token = result.get('token')
    _require_token(token)
    return token


def write_environment(destination, token):
    """Replace the token file atomically in a private, service-owned directory."""
    _require_token(token)
    destination = Path(destination)
    pending = None
    try:
        parent = destination.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
                or parent.st_uid != os.geteuid()):
            raise IssuerError('project token directory must be service-owned with mode 0700')
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', dir=destination.parent,
                                             prefix='.project-', delete=False) as stream:
                pending = stream.name
                os.fchmod(stream.fileno(), 0o600)
                stream.write(f'FACTORY_PROJECT_TOKEN={token}\n')
            os.replace(pending, destination)
            pending = None
        finally:
            if pending is not None:
                os.unlink(pending)
    except OSError:
        raise IssuerError('cannot write the project token environment file') from None


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1:
            raise IssuerError('issuer must run as root with no arguments')
        credential_directory = os.environ.get('CREDENTIALS_DIRECTORY')
        if not credential_directory or not Path(credential_directory).is_absolute():
            raise IssuerError('systemd credential directory is unavailable')
        try:
            key_pem = (Path(credential_directory) / 'github-app.pem').read_bytes()
        except OSError:
            raise IssuerError('cannot read the systemd GitHub App credential') from None
        token = mint_token(os.environ.get('FACTORY_APP_ID'), os.environ.get('FACTORY_PROJECT_ORG'),
                           key_pem)
        write_environment(_ENVIRONMENT, token)
    except IssuerError as error:
        print(f'Project token refresh failed: {error}', file=sys.stderr)
        return 1
    except Exception:
        print('Project token refresh failed: unexpected issuer error', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
