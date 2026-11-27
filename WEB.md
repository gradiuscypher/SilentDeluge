# Local inventory web app

Python 3.11+, standard library only. The app listens on **`127.0.0.1` only**. By default, open the exact HTTP URL it prints: `localhost` and other hostnames are not accepted. For remote browser access, use the explicit [same-host TLS reverse proxy mode](#serve-behind-a-same-host-tls-reverse-proxy); do not expose the backend by port forwarding. To switch accounts in a browser, use separate browser profiles or close the private browsing session; HTTP Basic has no app logout button.

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

## Serve behind a same-host TLS reverse proxy

Opt in to one external HTTPS origin:

```sh
python3 web_inventory.py serve --config "$private/web.json" --port 8765 \
  --public-origin https://inventory.example.test
```

- Use a lowercase DNS hostname (ASCII, or Punycode for an internationalized name) for browser compatibility. No credentials, IP literals, explicit port (even `:443`), trailing dot, path (even `/`), query, or fragment. The app prints the configured HTTPS URL but still serves plain HTTP only on `127.0.0.1:8765` in this example.
- Terminate TLS on the same host, on the default HTTPS port with a certificate for that name. Route only the configured hostname to the loopback backend. Preserve the client's `Host: inventory.example.test` and pass `Origin` through unchanged when present; never add or rewrite `Origin` to make a request pass. Do not log Basic credentials or cache inventory responses.
- The app compares `Host` exactly on every GET and POST, and requires the exact `Origin: https://inventory.example.test` on POSTs. Missing or mismatched values fail; `Forwarded` and all `X-Forwarded-*` headers are ignored. Authentication, CSRF tokens, and scope grants are unchanged. Omit `--public-origin` to use the original loopback HTTP origin.

## Private configuration and permissions

- Keep `web.json`, provider inputs and credentials, and the entire SQLite state directory **outside Git**. The web config and database must be private regular files owned by the serving OS user (use `0600`, no symlinks); use an existing private `state_dir` (`0700`, absolute path, no symlink). One server per state directory. Stop the server before editing configuration, and restart to apply changes. Keep the configured source ID stable for the same evidence stream; use a new ID if repointing it to a different stream.
- Each `users` key is a local username. `password_hash` is the salted PBKDF2-SHA256 verifier emitted by `hash-password`, never a plaintext password. `view_scopes` grants access to source cards and authorization to enable or disable **that user's** evidence view. Views start disabled. `collect_scopes` is a separate subset of `view_scopes`; use `[]` for a view-only account. Grants are exact strings and apply to allowlisted sources in that scope, not to subjects named in sightings.
- `sources` is the server allowlist. Each ID fixes the `provider` (`incus`, `unifi`, or `linux`), exact `scope`, operator-authored `read` description, and provider `config`. Put the scope on the source, not inside `config`. For collectable sources, the preview shows `read`: describe the actual approved reads, not paths, credentials, or other secrets. Use only read-only provider setups from [COLLECTION.md](COLLECTION.md); use absolute paths for any configured files. Requests can select an allowed source ID, never a file path, endpoint, command, or provider. Use `{}` for an empty installation.
- Set `"import_only": true` and `"config": {}` for evidence collected elsewhere. Keep a `read` description of the source for operators. The card shows that collection is disabled, and the server refuses preview and collect even for an enabled viewer with `collect_scopes` for that scope. Scope grants and per-user enablement still control who can view evidence. The default is `import_only: false`.
- `declarations` is optional and only used for Incus instance comparisons. It can be inlined in the matching scope using the offline declaration shape (`source`, `scope`, `timestamp`, `containers`, `vms`). Inventory works without declarations; even with them, runtime sightings do not establish instance absence.
- Enable and disable affect viewing only. For a collectable source, an enabled viewer with a collection grant can preview one manual read before confirming it. The preview states the allowed read, scope, and evidence destination. Confirmation expires after five minutes, is single-use, and never starts probes, schedules, guest access, or other monitoring. Browser POSTs need the exact served `Origin` (loopback HTTP by default, configured HTTPS in proxy mode) and an account-specific CSRF token. No request adds grants.
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

- `--source` is the configured ID, not the envelope's provider name. Import is also available for collectable sources. It writes to the same private database without a server restart or SQL commands; it does not enable any viewer. After enabling the source, reload the dashboard to see subsequent imports.
- Import requires the exact version-1 `collection.py` envelope, with `source` matching the configured provider and `scope` matching exactly. The envelope timestamp and any section timestamps need explicit UTC offsets. Section names must be distinct; outcomes must be `complete`, `partial`, or `failed`; records must be objects, and failed sections must have no records. Complete Incus instance rows need unique instance names, kind, and state; effective limits, when set, are strings. Additional record fields are retained. Invalid input exits `2` without updating evidence. These checks enforce the contract, not the origin or truth of a report; import only approved files.
- A section's timestamp takes precedence over the envelope timestamp; otherwise the envelope timestamp is the capture time. Import never dates evidence with the import time. Equal or older section captures do not replace retained evidence. Empty, failed, or partial reports are not evidence that omitted things are absent. The import command never invokes a provider or falls back to a live read. There is no HTTP import, upload, path selection, guest access, or schedule.
- The browser shows row counts, capture times, and Linux usage units (seconds, percent, cores, and bytes). Incus declaration comparison is separate from the full observed rows, so it does not hide provider context. A dash means a field was omitted; `null`, `false`, zero, and empty strings (`""`) are distinct. Package rows are collapsed by default behind a keyboard-operable **Show … packages** disclosure. Expanding it reveals the whole capture, not a sample; all rows are delivered in the authenticated response.

## Acceptance on synthetic inputs

Automated CLI and HTTP scenarios use only temporary files, loopback, synthetic Incus and Linux envelopes, and the saved synthetic runtime example:

```sh
python3 -m unittest -v test_web_inventory
```

Browser acceptance at `http://127.0.0.1:8765`:

1. Open as Alice. Only `stage` appears; enable its view and verify **Not connected**. Open **Preview one collection**. Check the configured read, exact scope, permission, and local evidence destination. Return to inventory without confirming and verify it is still disconnected.
2. Preview again, then select **Collect once**. Verify `paper-moon` disagrees on CPU, `tin-piano` is absent, and evidence cites `stage`, `theatre/stage`, and the original capture time and age. Do not use a new capture timestamp merely to refresh old evidence.
3. In the setup shell after stopping the server, copy `"$private/stage-failed.json"` over `"$private/stage.json"` and restart with the same command. Preview and collect again. The section reports failure, `paper-moon` is stale with its original observed fields and timestamp, and `tin-piano` is unknown, not absent. Disabling the view hides those records; re-enabling restores them without collecting.
4. Open as Bob in a separate profile or private session. Only `booth` appears. Enable, preview, and collect; `sound-mixer` is observed without declarations. No stage source, scope, records, or actions are exposed. `/patterns` displays synthetic examples; `/style.css` serves the local stylesheet without external resources.
5. For empty first-run behavior, stop the server and start with a new `0700` state directory and `"sources": {}` while keeping the users. Sign in and verify no sources are available; there is no prompt to create declarations or run a discovery scan.
6. With the import-only example sources configured, use approved synthetic envelopes with distinguishable capture times. Verify imported rows are hidden before enabling; Incus context is visible alongside any comparison; the package disclosure expands with Enter or Space and includes the last row; zero usage and null limits are not blanks. Verify the import-only card has no preview, including for a collector account. The automated scenarios also check direct preview/collect denial, no provider fallback, invalid imports, and stale evidence after partial, failed, omitted, and replayed sections. They do not test live collection, transport, or deployments.

### CLI smoke: submit the same forms with curl

Run in a second terminal against a fresh synthetic startup. `--user alice` prompts for the password; do not put the password on the command line. The first GET must return `401`, both mutating requests (`enable` and `collect`) must return `303`, and the final GET must show `paper-moon` and `tin-piano`.

```sh
origin=http://127.0.0.1:8765
curl --noproxy '*' -isS "$origin/"
dashboard=$(curl --noproxy '*' -fsS --user alice "$origin/")
csrf=$(printf '%s\n' "$dashboard" | grep -o 'name="csrf" value="[^"]*"' | head -n 1 | cut -d '"' -f 4)
curl --noproxy '*' -isS --user alice -H "Origin: $origin" \
  --data-urlencode "csrf=$csrf" --data 'source=stage' "$origin/enable"
preview=$(curl --noproxy '*' -fsS --user alice -H "Origin: $origin" \
  --data-urlencode "csrf=$csrf" --data 'source=stage' "$origin/preview")
printf '%s\n' "$preview" | grep -E 'Configured read|Evidence destination'
confirmation=$(printf '%s\n' "$preview" | grep -o 'name="preview" value="[^"]*"' | cut -d '"' -f 4)
curl --noproxy '*' -isS --user alice -H "Origin: $origin" \
  --data-urlencode "csrf=$csrf" --data 'source=stage' \
  --data-urlencode "preview=$confirmation" "$origin/collect"
curl --noproxy '*' -fsS --user alice "$origin/" | grep -E 'paper-moon|tin-piano'
```
