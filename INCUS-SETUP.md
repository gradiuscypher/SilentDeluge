# Incus inventory and metrics setup

Choose one approved server (or cluster) and project. Record their mapping to
`server/project`, the installed Incus version, and the fields and destination
the operator approves. Declarations are optional and grant no reads. Neither
the collection CLI nor its Incus provider contacts Incus. Keep real exports,
captures, configs, endpoint names, credentials and acceptance records private.

| Path | Identity | Evidence |
| --- | --- | --- |
| Operator pushes a fixed-field inventory export | Existing approved operator-side access with whole-project visibility | Name, kind, state, effective CPU/memory limits and optional instance metadata, including stopped guests |
| Separate metrics capture | Dedicated TLS trust entry of type `metrics`, not `client` | Running guest sightings and available measurements; no effective limits, stopped guests, or evidence of absence |

## Whole-project inventory: operator-pushed export

1. On the operator side, confirm the explicit Incus remote alias points to the
   approved server and that the project exists. Use the approved local Incus
   socket or a remote whose server certificate was verified out of band; never
   accept an unknown certificate. Check that the operator identity can see
   every instance in the project, including stopped guests and all cluster
   members. A successful list may still be filtered by authorization.
2. Approve one read of `GET /1.0/instances?recursion=1&project=…` without a
   member, instance, kind or state filter. Use existing approved access or an
   operator-side identity with only the needed reads if supported. Do not give
   the consumer the Incus socket, group membership, SSH access, client
   configuration, or any Incus credential. Do not open a listener for this path.
3. Run the reusable exporter from this checkout **as the operator**, writing
   to an existing private directory (mode `0700`). Use a fresh output name:

   ```sh
   python3 source_incus.py --remote "$approved_remote" \
     --scope "$approved_server/$project" --confirm-whole-project \
     --output "$operator_dir/snapshot.json"
   ```

   The exporter records UTC time at the read, checks the response, and writes
   only `source`, `scope`, `timestamp`, `outcome` and `instances`. Each instance
   has `name`, `type`, `status`, and `expanded_config` containing only
   `limits.cpu` and `limits.memory` when set. Optional fields are `architecture`,
   `location` (cluster member), `profiles` (ordered names, no profile contents),
   `ephemeral` (boolean), `created_at` and `last_used_at` (Incus's last start
   timestamp). Timestamps keep their supplied offsets; missing fields, empty
   architecture/location and Incus's zero-time sentinel are omitted.

   Every API instance must include an `expanded_config` mapping. An explicit
   `{}` is valid; omission leaves effective limits unknown. If any instance
   omits the mapping, the exporter fails without publishing a snapshot.

   An API `project` field, when present, must exactly match the project in
   `--scope`; the export keeps the scope, not the redundant project field.
   If Incus does not report a project, the scope is still the operator's
   attestation. Neither check proves the remote alias or the identity's
   whole-project visibility.

   The raw API response stays in operator-side memory. The output is atomically
   replaced on success and is private (mode `0600`). On error nothing new is
   published; never reuse an old file as evidence of a new read.
4. Review the new file privately and push just that file to the consumer using
   the approved transfer path. Keep it in a private, operator-controlled
   location readable by the collection account. Stage updates and rename them
   into place; the consumer must not be able to rewrite the evidence.

[`examples/complete.json`](examples/complete.json) is the export format.
`complete` is an attestation of a successful unfiltered whole-project read.
An empty `complete` export is valid only when the confirmed project really is
empty. If visibility or enumeration is incomplete, export only validated
positive rows under `partial`, never `complete`. For a failed attempt, use
`failed` with an empty `instances` array and the actual attempt timestamp.
Neither `partial` nor `failed` proves absence or displaces the last verified
complete inventory.

To revoke this path, stop the operator export and disable the push route;
remove any dedicated transfer grant on both ends. Revoke any identity created
solely for the operator-side read using the installed authorization system.
Do not revoke unrelated operator access. Retain or remove saved evidence under
the installation's retention policy.

## Runtime metrics: dedicated TLS identity

Use only if separately approved. Consult the installed version's [metrics
guide](https://linuxcontainers.org/incus/docs/main/metrics/) and
[authorization guide](https://linuxcontainers.org/incus/docs/main/authorization/).
Prefer an existing private metrics-only listener (`core.metrics_address`) with
`core.metrics_authentication=true` and network access limited to the approved
capture host. Do not enable anonymous metrics or expose the full API for this.
New listeners, firewall changes and broader visibility need separate approval.

1. As the account that will take the capture, create a dedicated key and
   certificate in a fresh private directory (`0700`). Keep the key `0600`, never
   transfer it to the Incus admin or offline consumer, and do not reuse an
   inventory API identity. For example, with OpenSSL and an approved lifetime:

   ```sh
   umask 077
   openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 \
     -sha384 -nodes -days 90 -subj '/CN=silentdeluge-metrics' \
     -keyout "$tls_dir/metrics.key" -out "$tls_dir/metrics.crt"
   ```

2. Transfer only `metrics.crt` to the Incus operator over an authenticated
   channel. Compare its SHA-256 fingerprint on both sides
   (`openssl x509 -in metrics.crt -noout -fingerprint -sha256`). From the
   operator's admin context on the approved server:

   ```sh
   incus config trust add-certificate "$received_metrics_crt" \
     --type=metrics --name=silentdeluge-metrics --restricted --projects "$project"
   incus config trust list -c ntFrpE
   ```

   Record the entry's full fingerprint, type, project restriction and expiry.
   Verify the effective permissions in the installed release: metrics GET
   only, no instance API, config, lifecycle, console, exec or guest-file
   access. Check which project and daemon-level metrics the endpoint can
   disclose. `?project=…` requests a filter; it is not an authorization
   control. If you cannot establish that effective visibility fits the
   approved scope, remove the trust entry and leave metrics disconnected.
   Do not test denials by attempting prohibited API operations.
3. Obtain the server/listener certificate or cluster certificate and trust
   chain from the operator, not from an unverified connection. Compare the
   leaf's SHA-256 fingerprint out of band before deriving its public-key pin.
   Use the trusted CA as `server-ca.crt` (the verified server certificate for
   a self-signed listener) and a URL hostname covered by the certificate:

   ```sh
   openssl x509 -in "$tls_dir/server.crt" -noout -fingerprint -sha256
   # After comparison, save the approved leaf public key for curl.
   openssl x509 -in "$tls_dir/server.crt" -pubkey -noout > "$tls_dir/server.pub"
   ```

   Keep the pin and CA in a directory writable only by the capture account or
   operator. Require hostname, trust chain and pin verification; no `-k`,
   redirects, blindly refreshing the pin, or falling back to another endpoint.
4. After approval, take **one** capture to a new private directory. Use an
   approved HTTPS URL ending in `/1.0/metrics`, with no query string. Keep the
   timestamp with the bytes; use only the completed capture:

   ```sh
   (
     set -eu
     umask 077
     capture=$(mktemp -d "$capture_root/metrics.XXXXXXXX")
     trap 'rm -f -- "$capture/runtime.prom" "$capture/captured-at"; rmdir -- "$capture"' EXIT
     captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
     status=$(curl --disable --silent --show-error --fail --proto '=https' \
       --noproxy '*' --cacert "$tls_dir/server-ca.crt" --pinnedpubkey "$tls_dir/server.pub" \
       --cert "$tls_dir/metrics.crt" --key "$tls_dir/metrics.key" \
       --get --data-urlencode "project=$project" --write-out '%{http_code}' \
       --output "$capture/runtime.prom" "$metrics_url")
     test "$status" = 200
     printf '%s\n' "$captured_at" > "$capture/captured-at"
     trap - EXIT
     printf 'Saved capture: %s\n' "$capture"
   )
   ```

   Do not print or publish the raw capture. Preserve the endpoint/member and
   scope in the private record; metrics labels identify the project, not the
   server. On a cluster, a scrape sees only the selected member. No scrape,
   or lack of sightings within one, can prove guest absence.
5. Rotate before expiry: create a new key/cert, approve and enroll it with the
   same restrictions, then do a separately approved one-read acceptance before
   switching. Remove the old server trust entry by its full fingerprint
   (`incus config trust remove "$old_fingerprint"` in the operator context);
   verify removal in the trust list and securely remove old local credentials.
   For compromise or decommission, stop capture and revoke first, then remove
   the key and any dedicated route. Treat pin mismatch as a stop: verify an
   intended server certificate/key change out of band before replacing the pin.

## Offline import and one-read acceptance

The private config for `python3 collection.py incus --config "$config"` may
contain either or both inputs. Example values:

```json
{
  "scope": "theatre/stage",
  "snapshot_path": "/private/incus/snapshot.json",
  "metrics_path": "/private/incus/runtime.prom",
  "metrics_timestamp": "2025-01-01T10:05:00Z"
}
```

For export-only, omit both `metrics_*` fields. For metrics-only, omit
`snapshot_path` and use the value from `captured-at` for `metrics_timestamp`.
The scope must match the snapshot exactly; metrics require explicit
`server/project` and only matching project labels contribute guest evidence.
Instance records retain the optional inventory fields when supplied. The
wrapper's UTC timestamp dates the import; the `instances` and `runtime`
section timestamps date the evidence and may differ.

Runtime is always `partial`, including when there are no sightings. Each
guest has at most one row. `measurements` retains only the following
[Incus instance metrics](https://linuxcontainers.org/incus/docs/main/reference/provided_metrics/#instance-metrics):

| Field | Meaning |
| --- | --- |
| `memory_rss_bytes` | Container RSS as reported by `incus_memory_RSS_bytes`, in bytes; not total memory use |
| `memory_available_bytes` | VM guest-available memory in bytes, retained only with a positive guest clock and reported total memory; QEMU-only fallback estimates are excluded |
| `cpu_seconds_total` | Cumulative `incus_cpu_seconds_total` user/system counters in seconds, per reported numeric `cpu_index` or without an index for an aggregate series. Incus can report VM vCPU thread counters from the host when the agent is unavailable. These are neither utilization nor a configured limit. |
| `uptime_seconds` | `incus_time_seconds` minus `incus_boot_time_seconds` when both are reported for the same guest and the boot time is positive and not after the guest clock. Not derived from scrape time, daemon uptime or inventory timestamps. |

Missing measurements are omitted, not filled with zero. Measured rows include
`capture` with `source: "incus-prometheus"`, the configured `scope`, and the
operator-supplied capture `timestamp` (not import time or an exact sampling
time; Incus may cache metrics). No raw labels, raw config, endpoint or file
paths are included in records. Keep raw captures private. A capture containing
only legacy CPU sightings without a mode or boot times without a guest clock
still imports as sightings. Invalid inputs never yield an empty `complete`
section.

Before enabling either live path, preview the approved scope, identity, exact
read, effective permissions and retained fields. Accept each path with one
operator-approved read, not a recurring job. Privately verify the export
includes stopped guests, inherited limits and optional fields if present.
For metrics, check that only scoped positive sightings appear and compare any
measurements to the corresponding guest samples and units. Check capture
times survive import unchanged. Use synthetic replays to verify incomplete
evidence and project-mismatch handling rather than inducing a live failure.
Record version, endpoint, identity or transfer reference, verified grants,
capture time, observed result and revocation steps; mark any unverified
property explicitly. Report only sanitized status. Live acceptance does not
authorize scheduling or guest inspection.
