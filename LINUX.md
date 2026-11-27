# Local Linux guest inventory

Run the `linux` source **inside each enrolled guest** with Python 3.11+ and a
normal, unprivileged account. The same command works in an Incus VM or
container and on another provider. Install the checkout and configuration in
the guest; do not run the collector on the hypervisor to represent its guests.
It does not need Incus credentials, a hypervisor socket, extra capabilities, or
host mounts. Do not elevate permissions to fill a gap in coverage.

Create a private configuration file, for example:

```json
{
  "scope": "toy-theatre/paper-moon",
  "asset": "paper-moon",
  "packs": ["packages", "cpu", "memory", "disk", "users", "interfaces"],
  "package_manager": "dpkg",
  "resource_view": "procfs"
}
```

```sh
python3 collection.py linux --config /path/to/private/linux.json
```

The sample selects every available pack; remove the entries you have not
approved. Omit `packs` or set it to `[]` to collect nothing. Keep `scope`
unique to the enrolled guest and stable across renames and host moves. The
optional `asset` appears in each record and defaults to `scope`. Neither is
inferred from the hostname, IP address, machine ID, or hosting provider. Give
a replacement guest a new identity. Keep actual configuration and output in
the private installation. Approve scheduling separately from the initial read.

## Packs and units

| Pack | Evidence and scope |
| --- | --- |
| `packages` | Installed native package name, version, architecture and manager. This includes libraries tracked by the selected package manager. Set `package_manager` to `dpkg` (Debian/Ubuntu, `/var/lib/dpkg/status`) or `apk` (Alpine, `/lib/apk/db/installed`). The collector does not try other databases if the configured one is unavailable. RPM and other backends, application-level package managers, and unmanaged libraries are not covered. |
| `cpu` | Two counter reads about 0.2 seconds apart; `sample_seconds` is the actual elapsed monotonic time. In `procfs`, `busy_percent` is a percentage of aggregate visible CPU time, excluding idle, I/O wait, and steal from busy time. In `cgroup-v2`, `cpu_cores_used` is average CPU seconds per wall second and may exceed one. Neither value is CPU capacity or an Incus limit. |
| `memory` | In `procfs`, `total_bytes` and `available_bytes` are the visible `MemTotal` and `MemAvailable`; `used_bytes` is their difference (an estimate). In `cgroup-v2`, `used_bytes` is the current charge (including charged cache); `limit_bytes` is the direct cgroup limit, `null` if `memory.max` says `max`. Ancestor limits may still apply. |
| `disk` | `statvfs` for `/` only: filesystem `total_bytes`, allocated `used_bytes`, and `available_bytes` to unprivileged users. Reserved blocks can make used plus available less than total. This is the root filesystem view, not whole-guest disk use or a container's quota; overlay or shared filesystems can report backing capacity and usage. No mount enumeration, disk walks, or I/O sampling. |
| `users` | Name, UID and primary GID from the guest's `/etc/passwd` only. Includes service accounts. No NSS queries, directory users, groups, login history, password fields, GECOS, home paths or shells in evidence. |
| `interfaces` | Name and index in the collector's current network namespace, including loopback. Indices can change. No addresses, MACs, routes, DNS, counters, traffic capture or remote queries. |

## Choosing the resource view

- Prefer `resource_view: "procfs"` in a VM. In a container, use it only if
  both `/proc/stat` and `/proc/meminfo` are known to be virtualized for that
  guest (for example, by its Incus/LXCFS setup). Otherwise they may represent
  the host. The collector cannot infer what those files represent.
- Use `resource_view: "cgroup-v2"` only when `/sys/fs/cgroup` inside the
  guest is rooted at that guest's cgroup and exposes the corresponding
  controller files. A shared host mount or a mount limited to a single service
  is not a guest view. Cgroup v1 and automatic cgroup discovery are unsupported.
- If neither view is known to be guest-scoped, leave CPU and memory disabled or
  leave `resource_view` unset to report those selected sections as unknown.
  The collector never falls back from one view to another.

The runner adds outer `source`, `scope` and UTC collection timestamp. Each
selected pack returns its own `section`, `outcome` and `records`. `complete`
means the pack read its documented local view; a complete inventory may be
empty. An unsupported, unreadable or malformed input yields `failed` with no
records for that pack, meaning **unknown**, not zero usage or observed absence.
Raw input and failure details do not appear in output. Disabled packs are absent
from the report. The packs are independent and are not an atomic snapshot.

This is guest evidence, distinct from hosting-provider inventory. An Incus
[fixed-field scan](examples/complete.json) describes instance kind, status and
effective configured limits, not what the guest is running or using. Placement
and matching between provider records and enrolled guests are not inferred.

The collector does not spawn subprocesses, read shadow files, collect
credentials, inspect process command lines or environments, discover guests or
peers, use SSH, or invoke guest exec. There is no collector-side network
transport or upload.
