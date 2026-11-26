# SilentDeluge milestones

Once configured, routine collection, rule evaluation, finding updates, UI status, and configured notifications run without a live agent harness or model provider. Agents add investigation and recommendations; security detections do not depend on them.

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

UniFi and endpoint collection come after this milestone.
