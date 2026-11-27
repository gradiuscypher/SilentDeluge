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
  "packs": [
    "packages", "cpu", "memory", "disk", "users", "interfaces",
    "system", "storage", "services", "network_links"
  ],
  "package_manager": "dpkg",
  "resource_view": "procfs",
  "service_manager": "systemd"
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
| `system` | One record with `os_id` and `os_version` from `ID` and `VERSION_ID` in `/etc/os-release` (or `/usr/lib/os-release` if absent); `os_version` is `null` if not set. `kernel_release` and `architecture` are the kernel's `uname` release and machine values, not package architecture. `boot_time_unix_seconds` is `/proc/stat` `btime` (integer seconds since the Unix epoch); `uptime_seconds` is `/proc/uptime` elapsed seconds since boot with centisecond precision. |
| `storage` | One record per eligible filesystem mount at `/`, `/boot`, `/boot/efi`, `/usr` or `/var` in the current mount namespace. Fields: `path`, `filesystem` (Linux filesystem type), `total_bytes`, `used_bytes`, `available_bytes` with the same units and reservation semantics as `disk`. |
| `services` | System-wide native service name and metadata state. Fields: `manager`, `name`, `state`. Set `service_manager` to `systemd` (the default) for installed definitions, or `openrc` for started markers. These states are not service health checks. |
| `network_links` | `name` and `ifindex` as in `interfaces`, plus Linux `operstate`, configured `mtu_bytes`, numeric `arphrd_type`, and cumulative `rx_bytes`/`tx_bytes`. Includes loopback. This opt-in does not change the fields or reads of `interfaces`. |

### System and boot scope

Requires readable OS release metadata and procfs plus an unprivileged `uname`.
The OS ID/version describe the guest's userspace; a container normally shares
its host kernel. Boot time and uptime describe the procfs view. They can
describe the host's boot, or be virtualized differently in a container; they
are not a guest start timestamp and are not reconciled. No hostname, machine
ID or boot ID is reported.

### Storage scope

Requires readable `/proc/self/mountinfo` and search access to the scoped mount
paths; no directory listings or tree walks. `/` must be listed; other paths
are queried only when they are mountpoints in their own right. Symlink
components are not followed. Non-root subtree mounts (including bind-mounted
subtrees or subvolumes) at the optional paths are skipped. No `/home`,
`/run`, `/etc`, removable media trees, or arbitrary configured paths are
queried. Device and mount source names, mount roots and options are not
included in evidence.

Supported filesystem types: `bcachefs`, `btrfs`, `erofs`, `exfat`, `ext2`,
`ext3`, `ext4`, `f2fs`, `jfs`, `ntfs3`, `overlay`, `squashfs`, `tmpfs`,
`vfat`, `xfs`, and `zfs`. A remaining scoped mount with an unsupported type
or ambiguous stacked mounts makes the section unknown; there is no fallback.
Capacity is per visible filesystem, not total host or guest disk capacity.
Do not sum rows: filesystems can share backing storage and may not reflect
container quotas. Mount enumeration and capacity reads are not atomic.

### Service coverage

- **systemd:** Requires search access to the fixed system paths and permission
  to list their existing directories. Reads top-level entries only, in
  precedence order: `/etc/systemd/system`, `/run/systemd/system`,
  `/usr/local/lib/systemd/system`, `/usr/lib/systemd/system`, then
  `/lib/systemd/system`. A `/lib` symlink directly to `usr/lib` or `/usr/lib`
  is treated as the already checked vendor path. Reports `installed` for a
  regular unit file, `masked` for a direct symlink to `/dev/null`, and `linked`
  for another symlink without verifying its target. Does not report enablement,
  runtime state, user units, drop-ins, generated units, or concrete template
  instances (which can contain paths). At least one scoped directory must be
  readable.
- **OpenRC:** Requires search access and permission to list
  `/run/openrc/started`. Reports `started` for plain service names present
  as markers, not for installed but stopped services. Does not verify that a
  process is running. A missing marker directory is unknown, not empty.

Neither view follows service links, opens unit or init script contents,
queries user managers, or traverses users' home directories. Link targets are
not emitted. Other init systems and alternate service directories are
unsupported; no fallback to a different manager.

### Network link fields

Requires namespace interface enumeration and readable guest sysfs
`/sys/class/net` attributes for those links. Sysfs indices must match the
enumerated ones. `operstate` is the kernel's operational state (`up`, `down`,
`lowerlayerdown`, `dormant`, `testing`, `notpresent`, or the literal `unknown`).
`arphrd_type` is the Linux link-layer type (e.g. `1` for Ethernet framing or
`772` for loopback), not a virtual device classification. RX and TX values are
unsigned byte totals, not rates; they can reset when a link is recreated or
its driver resets. Fields are not sampled atomically. No addresses, MACs,
routes, DNS, payloads, or remote queries.

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
