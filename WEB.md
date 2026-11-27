# Local inventory web app

Python 3.11+, standard library only. The app listens on **`127.0.0.1` only**. Open the exact URL it prints; `localhost`, other hostnames, and forwarded origins are not accepted. Do not expose it through a reverse proxy or port forward. HTTP Basic authentication is for direct loopback use only. To switch accounts in a browser, use separate browser profiles or close the private browsing session; HTTP Basic has no app logout button.

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

## Private configuration and permissions

- Keep `web.json`, provider inputs and credentials, and the entire SQLite state directory **outside Git**. The web config and database must be private regular files owned by the serving OS user (use `0600`, no symlinks); use an existing private `state_dir` (`0700`, absolute path, no symlink). One server per state directory. Stop the server before editing configuration, and restart to apply changes. Keep the configured source ID stable for the same evidence stream; use a new ID if repointing it to a different stream.
- Each `users` key is a local username. `password_hash` is the salted PBKDF2-SHA256 verifier emitted by `hash-password`, never a plaintext password. `view_scopes` grants access to source cards and authorization to enable or disable **that user's** evidence view. Views start disabled. `collect_scopes` is a separate subset of `view_scopes`; use `[]` for a view-only account. Grants are exact strings and apply to allowlisted sources in that scope, not to subjects named in sightings.
- `sources` is the server allowlist. Each ID fixes the `provider` (`incus`, `unifi`, or `linux`), exact `scope`, operator-authored `read` description shown in the preview, and provider `config`. Put the scope on the source, not inside `config`. Describe the actual approved reads in `read`, not paths, credentials, or other secrets. Use only read-only provider setups from [COLLECTION.md](COLLECTION.md); use absolute paths for any configured files. Requests can select an allowed source ID, never a file path, endpoint, command, or provider. Use `{}` for an empty installation.
- `declarations` is optional and only used for Incus instance comparisons. It can be inlined in the matching scope using the offline declaration shape (`source`, `scope`, `timestamp`, `containers`, `vms`). Inventory works without declarations; even with them, runtime sightings do not establish instance absence.
- Enable and disable affect viewing only. An enabled viewer with a collection grant can preview one manual read before confirming it. The preview states the allowed read, scope, and evidence destination. Confirmation expires after five minutes, is single-use, and never starts probes, schedules, guest access, or other monitoring. Browser POSTs need the exact loopback `Origin` and an account-specific CSRF token. No request adds grants.
- The private `inventory.sqlite3` store retains per-user view preferences and per-source evidence across restarts. Evidence is shared only with enabled viewers granted that scope. The latest attempt and each section's coverage are separate from the last verified complete capture and its age. Partial sightings are shown separately. A failed, partial, omitted, or replayed section does not replace verified complete evidence or establish absence; only a newer complete instance capture can establish an omitted declaration's absence in that population and scope, as of its capture time.

## Acceptance on synthetic inputs

Automated HTTP scenarios use only temporary files, loopback, and the saved synthetic runtime example:

```sh
python3 -m unittest -v test_web_inventory
```

Browser acceptance at `http://127.0.0.1:8765`:

1. Open as Alice. Only `stage` appears; enable its view and verify **Not connected**. Open **Preview one collection**. Check the configured read, exact scope, permission, and local evidence destination. Return to inventory without confirming and verify it is still disconnected.
2. Preview again, then select **Collect once**. Verify `paper-moon` disagrees on CPU, `tin-piano` is absent, and evidence cites `stage`, `theatre/stage`, and the original capture time and age. Do not use a new capture timestamp merely to refresh old evidence.
3. In the setup shell after stopping the server, copy `"$private/stage-failed.json"` over `"$private/stage.json"` and restart with the same command. Preview and collect again. The section reports failure, `paper-moon` is stale with its original observed fields and timestamp, and `tin-piano` is unknown, not absent. Disabling the view hides those records; re-enabling restores them without collecting.
4. Open as Bob in a separate profile or private session. Only `booth` appears. Enable, preview, and collect; `sound-mixer` is observed without declarations. No stage source, scope, records, or actions are exposed. `/patterns` displays synthetic examples; `/style.css` serves the local stylesheet without external resources.
5. For empty first-run behavior, stop the server and start with a new `0700` state directory and `"sources": {}` while keeping the users. Sign in and verify no sources are available; there is no prompt to create declarations or run a discovery scan.

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
