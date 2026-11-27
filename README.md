# SilentDeluge

SilentDeluge is a proposed open-source monitoring system for homelabs, personal computers, and network devices. Its first job is to answer a small question reliably: **which Incus guests did I declare, which were observed, and where do they disagree?**

**Status: offline Incus inventory slice.** The commands read local evidence files. Neither installs a collector or contacts a host.

- [Milestone plan](PLAN.md): first deliverable, local verification, and the public/private boundary.
- [Domain glossary](CONTEXT.md): agreed meanings and constraints for inventory, permissions, monitoring, and evidence.
- [Incus provider setup handoff](INCUS-SETUP.md): private identity provisioning, verification, and one-scan acceptance.
- [MIT license](LICENSE): license for original project material.

## Inventory from saved metrics

```sh
python3 metrics_inventory.py examples/runtime-declarations.json examples/runtime.prom \
  --scope theatre/stage --source synthetic-capture --timestamp 2025-01-01T10:00:00Z
```

Supply the capture's server/project and observation time, not the replay time.
CPU-time and boot-time samples identify running guests at capture time; repeated
series count once. A missing guest is unknown. Null observed limits mean unknown,
not unset. A null declaration means observed but undeclared. The JSON report is
partial evidence, not a scan input for the complete-inventory command below.
Keep real captures, declarations and output in a private directory outside Git.

## Try the inventory locally

From the repository root, with Python 3.11+ (standard library only):

```sh
python3 silentdeluge.py examples/declarations.json examples/baseline.json examples/complete.json
python3 silentdeluge.py examples/declarations.json examples/baseline.json examples/failed.json
python3 -m unittest -v
```

Both runs start with the same verified sighting of `paper-moon`. The complete scan establishes absence ten minutes later; the failed scan shows its last state and the age of that evidence **at the time of the attempt**. With no scan file, declared guests are **not connected**. Nothing is persisted between runs.

`inventory.compare(declarations, scans)` is the shared comparison path for the command and future UI and agent callers. Each envelope identifies its source, scope (one server/project), and timestamp with UTC offset. Kind comes from membership in `containers` or `vms`; omit `cpu` or `memory` to leave a limit undeclared. A `complete` scan asserts a successful listing of **all** instances in scope and supplies effective `expanded_config`, not just instance-local overrides. Supply scans in increasing timestamp order. Neither a `partial` nor a `failed` attempt supersedes verified evidence.

The first invariant, [`inventory.absence-requires-complete-scan`](test_inventory.py), is declared alongside its executable scenarios. Change them together.

**Private acceptance still required.** Privately preview and approve the mapping from the operator's instance lists; do not copy the lists into this repository. Verify the server/project, requested instance reads, and least-privilege credential scope before taking a real snapshot. If those reads cannot be scoped, keep declarations disconnected. Compare the same project's authorized read-only snapshot through this command, checking effective inherited limits, stopped guests, and complete versus incomplete evidence. No guest probes, lifecycle actions, or changes to the project are needed.

## Direction beyond the first milestone

Start with static inventory. Later integrations can add UniFi, Ubuntu, Windows, and macOS observations, then live metrics, process data, and logs. Prefer existing collectors and storage engines when they fit. Security detections should consume the same authorized logs as interactive queries, not require a second collection pipeline. Python detection rules are intended to run without an AI provider.

Operators should be able to onboard sources through their agent harness of choice and use the same permission-scoped capabilities from chat or the web UI. Integration skills and scripts should be reusable; agents may prepare new tools, but that does not grant permission to run them unattended. Start with local multi-user authentication and role-based access; leave room for single sign-on later.

The UI should follow the GRDS design direction, with editorial layouts for investigation reports. Discord is the first intended notification destination; other channels should be possible without changing the finding lifecycle. Infrastructure management is a later capability, separate from advisory monitoring. Technology choices and scheduling beyond the first milestone remain open.

Use local checks first. GitHub issues, Projects, pull-request automation, and hosted CI belong to a later development phase, not this handoff.
