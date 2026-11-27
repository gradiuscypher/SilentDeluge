# SilentDeluge

SilentDeluge collects inventory for homelabs, personal computers, and network devices. Start with **what an approved source can observe**. Optionally compare those observations with declarations; no hand-built first inventory is required.

Start with the [collection guide](COLLECTION.md) to select a configured source. The offline examples below demonstrate the separate Incus comparison path without contacting any host.

- [Milestone plan](PLAN.md): first deliverable, local verification, and the public/private boundary.
- [Domain glossary](CONTEXT.md): agreed meanings and constraints for inventory, permissions, monitoring, and evidence.
- [Incus provider setup handoff](INCUS-SETUP.md): private identity provisioning, verification, and one-scan acceptance.
- [Workspace](WEB.md) and [access model](ACCESS.md): sign in, browse a class of evidence, understand effective grants and preview collector setup.
- [Agent maintenance](AGENT-MAINTENANCE.md): update enrollment, platform and orchestration instructions whenever their contracts change.
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

**Private acceptance:** a one-time fixed-field export and a separate metrics capture have been exercised privately. Each installation must verify its own source scope, credential restrictions and completeness. Neither path grants permission to enter guests or schedule reads.

## Direction beyond the first milestone

Inventory precedes live monitoring and alerts. Future integrations can add Windows and macOS observations, process data, and logs. Prefer existing collectors and storage engines when they fit. Security detections should consume the same authorized logs as interactive queries, not require a second collection pipeline. Python detection rules are intended to run without an AI provider.

Operators should be able to onboard sources through their agent harness of choice and use the same permission-scoped capabilities from chat or the web UI. Integration skills and scripts should be reusable; agents may prepare new tools, but that does not grant permission to run them unattended. Start with local multi-user authentication and role-based access; leave room for single sign-on later.

The UI should follow the GRDS design direction, with editorial layouts for investigation reports. Discord is the first intended notification destination; other channels should be possible without changing the finding lifecycle. Infrastructure management is a later capability, separate from advisory monitoring. Technology choices and scheduling beyond the first milestone remain open.

To start or resume a GitHub Project, follow the [orchestrator runbook](ORCHESTRATION.md). Use synthetic local checks; hosted CI is a separate decision.
