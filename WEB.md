# Local inventory web app

Python 3.11+, standard library only. The app listens on **`127.0.0.1` only**. By default, open the exact HTTP URL it prints; `localhost` and other hostnames are not accepted. For remote browser access, use [same-host TLS reverse proxy mode](#serve-behind-a-same-host-tls-reverse-proxy); do not expose the backend by port forwarding.

## Start with two synthetic users

From the repository root, in a terminal. Choose two different throwaway passwords when prompted. This creates saved synthetic Incus captures, not a connection to Incus.

```sh
umask 077
private=$(mktemp -d /tmp/silentdeluge-web.XXXXXXXX)
mkdir -m 700 "$private/state"
alice_hash=$(python3 web_inventory.py hash-password) || exit
bob_hash=$(python3 web_inventory.py hash-password) || exit
ALICE_HASH="$alice_hash" BOB_HASH="$bob_hash" python3 - "$private" <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])

def write(name, document):
    (root / name).write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')

def capture(name, scope, minute, outcome, *instances):
    write(name, dict(scope=scope, timestamp=f'2025-01-01T10:{minute:02d}:00Z',
                     outcome=outcome, instances=instances))

def instance(name, cpu):
    return dict(name=name, type='container', status='Running', expanded_config={'limits.cpu': cpu})

stage, booth = 'theatre/stage', 'theatre/booth'
capture('stage.json', stage, 0, 'complete', instance('paper-moon', '1'))
capture('stage-failed.json', stage, 10, 'failed')
capture('booth.json', booth, 0, 'complete', instance('sound-mixer', '2'))
write('web.json', {
    'state_dir': str(root / 'state'),
    'users': {
        'alice': dict(password_hash=os.environ['ALICE_HASH'], view_scopes=[stage], collect_scopes=[stage]),
        'bob': dict(password_hash=os.environ['BOB_HASH'], view_scopes=[booth], collect_scopes=[booth]),
    },
    'sources': {
        'stage': dict(provider='incus', scope=stage, read='Read the saved stage instance export.',
                      config=dict(snapshot_path=str(root / 'stage.json')),
                      declarations=dict(source='synthetic declarations', scope=stage,
                                        timestamp='2025-01-01T09:00:00Z', vms=[],
                                        containers=[dict(name='paper-moon', cpu='2'), dict(name='tin-piano')])),
        'booth': dict(provider='incus', scope=booth, read='Read the saved booth instance export.',
                      config=dict(snapshot_path=str(root / 'booth.json'))),
    },
})
PY
unset alice_hash bob_hash
python3 web_inventory.py serve --config "$private/web.json" --port 8765
```

## Sign in and use the workspaces

Open the printed URL and submit a configured username and password at `/login`. To switch accounts, choose **Log out**, then sign in as the other user. Logging out revokes that browser session immediately without changing evidence or view preferences. Sessions expire after eight hours, are cleared by a server restart, and are limited to eight per account (the oldest is revoked first). The browser cookie is host-only, `HttpOnly`, and `SameSite=Strict`; it also has `Secure` in HTTPS mode.

Five failed sign-ins in 30 seconds trigger `429` with `Retry-After` until that window ends. Blocked retries do not extend the window, revoke active sessions, or block other accounts. A sustained stream of failures can still delay new sign-ins for the targeted account.

| Path | Workspace |
| --- | --- |
| `/` | Overview: scope-filtered counts and freshness |
| `/machines` | Linux machine evidence |
| `/incus` | Incus instance evidence and declaration comparison |
| `/unifi` | UniFi device and client evidence |
| `/relationships` | Relationships supported by approved evidence |
| `/collectors` | Source enablement, collection permission, and manual collection previews |
| `/collectors/new` | Review collector requirements; does not add configuration or grants |
| `/access` | Your effective scope grants |
| `/patterns` | Synthetic design examples |

Follow workspace links for available source details and section filters. Search and detail parameters cannot expand your scope grants. Anonymous page requests redirect to `/login`; the inventory no longer accepts HTTP Basic credentials.

## Serve behind a same-host TLS reverse proxy

Opt in to one external HTTPS origin:

```sh
python3 web_inventory.py serve --config "$private/web.json" --port 8765 \
  --public-origin https://inventory.example.test
```

- Use a lowercase DNS hostname (ASCII, or Punycode for an internationalized name) for browser compatibility. No credentials, IP literals, explicit port (even `:443`), trailing dot, path (even `/`), query, or fragment. The app prints the configured HTTPS URL but still serves plain HTTP only on `127.0.0.1:8765` in this example.
- Terminate TLS on the same host, on the default HTTPS port with a certificate for that name. Route only the configured hostname to the loopback backend. Preserve the client's `Host: inventory.example.test` and pass `Origin` through unchanged; never add or rewrite `Origin` to make a request pass. Do not log sign-in bodies or session cookies, or cache inventory responses.
- The app compares `Host` exactly on every GET and POST, and requires the exact `Origin: https://inventory.example.test` on every POST, including sign-in. Missing or mismatched values fail; `Forwarded` and all `X-Forwarded-*` headers are ignored. Omit `--public-origin` to use the loopback HTTP origin without `Secure` cookies.

## Private configuration and permissions

- Keep `web.json`, provider inputs and credentials, and the entire SQLite state directory **outside Git**. The web config and database must be private regular files owned by the serving OS user (use `0600`, no symlinks); use an existing private `state_dir` (`0700`, absolute path, no symlink). One server per state directory. Stop the server before editing configuration, and restart to apply changes. Keep the configured source ID stable for the same evidence stream; use a new ID if repointing it to a different stream.
- Each `users` key is a local username. `password_hash` is the salted PBKDF2-SHA256 verifier emitted by `hash-password`, never a plaintext password. `view_scopes` grants access to source cards and authorization to enable or disable **that user's** evidence view. Views start disabled. `collect_scopes` is a separate subset of `view_scopes`; use `[]` for a view-only account. Grants are exact strings and apply to allowlisted sources in that scope, not to subjects named in sightings.
- `sources` is the server allowlist. Each ID fixes the `provider` (`incus`, `unifi`, or `linux`), exact `scope`, operator-authored `read` description, and provider `config`. Put the scope on the source, not inside `config`. For collectable sources, the preview shows `read`: describe the actual approved reads, not paths, credentials, or other secrets. Use only read-only provider setups from [COLLECTION.md](COLLECTION.md); use absolute paths for any configured files. Requests can select an allowed source ID, never a file path, endpoint, command, or provider. Use `{}` for an empty installation.
- Set `"import_only": true` and `"config": {}` for evidence collected elsewhere. Keep a `read` description of the source for operators. The card shows that collection is disabled, and the server refuses preview and collect even for an enabled viewer with `collect_scopes` for that scope. Scope grants and per-user enablement still control who can view evidence. The default is `import_only: false`.
- `declarations` is optional and only used for Incus instance comparisons. It can be inlined in the matching scope using the offline declaration shape (`source`, `scope`, `timestamp`, `containers`, `vms`). Inventory works without declarations; even with them, runtime sightings do not establish instance absence.
- `network_domain` is an optional, opaque identifier (1–128 printable characters, no leading or trailing whitespace). Use the same exact identifier only for sources explicitly approved to share an addressing domain. No domain is inferred from a scope, network name, or address. `placement` is an optional object containing exactly `scope` (a configured Incus source scope) and `name` (the instance name, 1–256 printable characters, no leading or trailing whitespace). A placement is available only to viewers granted both scopes. For example, a Linux guest source can set `"placement": {"scope": "theatre/stage", "name": "paper-moon"}`. Omit either optional field rather than setting it to `null`.
- Enable and disable affect viewing only. For a collectable source, an enabled viewer with a collection grant can preview one manual read before confirming it. The preview states the allowed read, scope, and evidence destination. Confirmation is tied to the session that requested the preview, expires after five minutes, is single-use, and never starts probes, schedules, guest access, or other monitoring. `POST /enable`, `/disable`, `/preview`, and `/collect` require the exact served `Origin`, a session cookie, and the session's `csrf` field. `POST /logout` requires the same protections and no source. No request adds grants.
- The private `inventory.sqlite3` store retains per-user view preferences and per-source evidence across restarts. Evidence is shared only with enabled viewers granted that scope. The most recently processed attempt and its section outcomes are separate from the last verified complete capture and its age. Partial sightings are shown separately. Failed, partial, omitted, and replayed sections do not replace verified complete evidence or establish absence. Only an accepted complete instance capture can establish an omitted declaration's absence in that population and scope, as of its capture time.

## Import approved evidence offline

Use the [collection runner](COLLECTION.md) in the already approved collection environment. For Incus, an approved saved snapshot can produce an envelope without contacting Incus. For Linux guest evidence, run the [Linux collector](LINUX.md) inside the enrolled guest, not on the web server. Save stdout from a successful run; exit `0` does not mean all sections succeeded.

```sh
# In the respective approved collection environment, not from the browser:
umask 077
python3 collection.py incus --config /private/incus.json > /private/stage-envelope.json
python3 collection.py linux --config /private/linux.json > /private/guest-envelope.json
```

Configure a fixed ID per evidence stream in `sources`, with each report's exact scope. For example:

```json
{
  "stage-baseline": {
    "provider": "incus", "scope": "theatre/stage",
    "read": "Import approved Incus project evidence.", "config": {}, "import_only": true
  },
  "guest-baseline": {
    "provider": "linux", "scope": "theatre/paper-moon",
    "read": "Import approved evidence collected inside the guest.", "config": {}, "import_only": true
  }
}
```

Grant each scope only to its approved viewers. Transfer the approved envelopes privately. On the web host, run as the OS user that owns the private web config and state, using regular, non-symlink input files owned by that user with no group or other access (`0600`):

```sh
python3 web_inventory.py import-evidence --config "$private/web.json" \
  --source stage-baseline --file /private/stage-envelope.json
python3 web_inventory.py import-evidence --config "$private/web.json" \
  --source guest-baseline --file /private/guest-envelope.json
```

- `--source` is the configured ID, not the envelope's provider name. Import is also available for collectable sources. It writes to the same private database without a server restart or SQL commands; it does not enable any viewer. After enabling the source in **Collectors**, reload its provider workspace to see subsequent imports.
- Import requires the exact version-1 `collection.py` envelope, with `source` matching the configured provider and `scope` matching exactly. The envelope timestamp and any section timestamps need explicit UTC offsets. Section names must be distinct; outcomes must be `complete`, `partial`, or `failed`; records must be objects, and failed sections must have no records. Complete Incus instance rows need unique instance names, kind, and state; effective limits, when set, are strings. Additional record fields are retained. Invalid input exits `2` without updating evidence. These checks enforce the contract, not the origin or truth of a report; import only approved files.
- A section's timestamp takes precedence over the envelope timestamp; otherwise the envelope timestamp is the capture time. Import never dates evidence with the import time. Equal or older section captures do not replace retained evidence. Empty, failed, or partial reports are not evidence that omitted things are absent. The import command never invokes a provider or falls back to a live read. There is no HTTP import, upload, path selection, guest access, or schedule.
- Provider workspaces present capture times and typed evidence rather than raw JSON. Follow section links for more detail, including paginated Linux installed packages and the Incus comparison. Declaration comparison is separate from observed rows; it does not hide provider context.

## Acceptance on synthetic inputs

Automated CLI and HTTP scenarios use only temporary files, loopback, synthetic Incus and Linux envelopes, and the saved synthetic runtime example:

```sh
python3 -m unittest -v test_web_inventory
```

Browser acceptance at `http://127.0.0.1:8765`:

1. Sign in as Alice. In **Collectors**, only `stage` appears. Enable its view and preview one collection. Check the configured read, exact scope, permission, and evidence destination. Return to **Collectors** without confirming; **Incus** must still have no capture.
2. Preview again and choose **Collect once**. Open `stage` in **Incus** and check its declaration comparison: `paper-moon` disagrees on CPU, and `tin-piano` is absent as of the complete capture. Do not change a capture timestamp merely to refresh old evidence.
3. Stop the server, copy `"$private/stage-failed.json"` over `"$private/stage.json"`, and restart. Sign in and collect again. The comparison must show `paper-moon` as stale and `tin-piano` as unknown, not absent. Disabling the evidence view hides the evidence; re-enabling restores it without collecting.
4. Use **Log out**, then sign in as Bob in the same browser. Only `booth` appears in **Collectors**. Enable, preview, and collect; `sound-mixer` is observed without declarations. No stage source, scope, records, or actions are exposed.
5. With a fresh private state directory and `"sources": {}`, sign in and verify no sources are available and no discovery is prompted. With import-only sources, verify imports appear only after enabling, and that manual collection is unavailable even with a collection grant.
