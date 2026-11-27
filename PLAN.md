# SilentDeluge milestones

Once configured, routine collection, rule evaluation, finding updates, UI status, and configured notifications run without a live agent harness or model provider. Agents add investigation and recommendations; security detections do not depend on them.

## Start with discovery, not a spreadsheet

The operator chooses a source and scope. The setup agent previews the requested
reads, effective permissions, fields retained and destination. Once approved,
use that configured access to collect a first baseline, even with **zero
declarations**. Show what was observed, when, and what could not be observed.
Optionally map existing declarations to compare intent; do not make users
retype discovered assets. Tracking, guest inspection, recurring collection and
remediation are distinct choices. An empty, failed or stale response is not a
reason to remove an asset.

## What has been demonstrated

- The offline comparator explains effective Incus limits, stopped instances,
  declared differences, and stale evidence after incomplete attempts.
- A saved metrics capture produced a partial baseline of distinct running
  guests. Samples are not instances, usage is not a limit, and omissions are
  unknown. Synthetic examples exercise the same path.
- A one-time, operator-run export enumerated a private Incus project,
  including stopped guests, and compared it with approved declarations.
  Only name, kind, state, effective limits and provenance crossed the host
  boundary. No consumer admin identity, listener or recurring job was created.
- The modular collector now supports Incus snapshots, Linux guest packs and
  site-scoped UniFi device and client sightings. Linux packs have been exercised
  on both a VM and a container. UniFi's synthetic qualification is not a live
  acceptance: verifying the console's TLS identity is still a prerequisite.
- The authenticated workspace separates machine, Incus and UniFi evidence.
  Exact operator placements and time-bounded same-domain address candidates
  are distinct claims; neither grants access. Collector onboarding and future
  ACL/group/project controls are previews, not credential or policy editors.

For source setup and the agent's first-collection checklist, use
[COLLECTION.md](COLLECTION.md). Track delivery on the
[GitHub Project](https://github.com/orgs/silentdeluge/projects/1), not here.

## Public repository boundary

SilentDeluge's original code is MIT-licensed. Third-party components retain their own licenses and applicable notices.

Ship reusable integrations, onboarding skills, and a synthetic Incus example covering a match, a mismatch, and an undeclared guest. The example and UI must work without access to the operator's LAN or internal design site. Keep real homelab declarations, mappings, credentials, observations, and reports in the private installation.

## Verification

Start with locally runnable synthetic scenarios tied to declared invariants. For example, a complete scan omitting a declared guest can establish absence; a failed scan cannot. Validate observable results, not just that the import command exits successfully.

Give each invariant a stable name and a plain-language declaration. Its local synthetic tests reference that name; a change to the contract updates the declaration and the tests together. For `inventory.absence-requires-complete-scan`, exercise both the complete-scan and failed-scan outcomes above.

Before accepting the Incus milestone, exercise the same workflow privately against a real project with read-only access. Defer GitHub CI configuration until a later development phase; when added, it should run the local synthetic checks without homelab credentials, private inventory, or a LAN runner.

## 1. High-level Incus inventory

Answer: **Which guests did I declare, which does Incus report, and where do they disagree?**

- Compare guest declarations from the operator's instance definitions with a read-only Incus inventory. Preserve source, scope, and timestamp for each claim.
- The setup agent helps the operator onboard as many in-scope inventory objects as possible, then helps enroll new ones on request. It previews a reusable mapping from existing files (including guest kind inferred from list membership); the operator approves the mapping, not a duplicate inventory. Routine refreshes reuse it without an LLM. Onboarding declarations grants no collection permission.
- Declaration onboarding does not require live Incus credentials. Before activating unattended collection, show the source, project, requested reads, and credential scope. Require least-privilege access for those reads; if only a broader credential is available, retain the declarations and show live inventory as **not connected**, not absent.
- Compare guest kind (VM or container), CPU limit, and memory limit against effective Incus configuration, including inherited profile values. Compare equivalent limits by value, not spelling. If a limit is undeclared, show the observed value without flagging a discrepancy.
- Show a stopped guest as present and stopped. Show a guest found only in Incus as observed and undeclared, not unauthorized. Neither case is permission to start or probe the guest.
- Report a declared guest as absent only after a successful, complete check of the relevant scope. On a failed or partial check, retain the last observation and show its age.
- Give the same evidence-backed answer in the inventory UI and through the operator's agent.

## Delivery sequence

GitHub [issues](https://github.com/gradiuscypher/SilentDeluge/issues) and the
**SilentDeluge Inventory Foundations** Project own status. On a request to
start or resume project work, follow [ORCHESTRATION.md](ORCHESTRATION.md).

1. [Collection spine (#1)](https://github.com/gradiuscypher/SilentDeluge/issues/1):
   one declaration-optional entrypoint; providers own transport and extraction.
   Share provenance, scoped identity, section outcomes and error handling.
   Keep unlike units and authority scopes distinct.
2. In parallel, finish [Incus (#2)](https://github.com/gradiuscypher/SilentDeluge/issues/2)
   (repeatable fixed-field inventory export and separate metrics identity);
   add [UniFi (#3)](https://github.com/gradiuscypher/SilentDeluge/issues/3)
   (passive site-scoped infrastructure and connected-client sightings); and
   [Linux guest inventory (#4)](https://github.com/gradiuscypher/SilentDeluge/issues/4)
   (opt-in packages/libraries, resource usage, users and interfaces).
   Guest evidence belongs to the enrolled guest, not its hypervisor; a future
   cloud provider can report placement without being granted guest access.
3. [Web interface (#5)](https://github.com/gradiuscypher/SilentDeluge/issues/5):
   scoped source management and evidence inspection, followed by accessible
   inventory views. Use GRDS design patterns as a reference, not a runtime
   dependency. Ship a living pattern page backed by the UI's actual components.
4. Separately approve private acceptance and any scheduling per source. A
   synthetic qualification or an authorized one-time read is not an enrollment
   for unattended monitoring.
