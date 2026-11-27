# Keeping platform instructions current

Use this checklist when a change affects how an agent sets up, reads, configures or deploys SilentDeluge. GitHub issues and Projects track the work; guides teach the next operator. Neither grants permission to run an operation.

| Changed contract | Update in the same PR | Demonstrate before merge |
|---|---|---|
| Provider name, scope, config, reads, packs, units or credential | [Collector catalog and onboarding](COLLECTION.md) and the linked provider guide | A new agent can select the source and run the documented command on a synthetic input; an incomplete read reports uncertainty. |
| Evidence schema, freshness or cross-source identity | Provider guide, [domain vocabulary](CONTEXT.md), comparison or association examples | Replay an old report. Try a missing field, reused identifier and a viewer lacking the other scope. |
| Login, sessions, access, projects, groups or visible actions | [Web instructions](WEB.md) and [access model](ACCESS.md) | Check a permitted and a restricted account. Describe only enforced grants as effective; label modeled controls separately. |
| Navigation, filters, result density or reusable visual grammar | [Design reference](DESIGN.md) and `/patterns` | Reach the changed view by keyboard and narrow viewport. Verify filter, clear, empty state and evidence provenance. |
| Issue intake, assignment, worker privileges, promotion or deployment | [Orchestrator runbook](ORCHESTRATION.md), factory guide and deployment instructions | Demonstrate duplicate intake, a failed stage, restart and selection of the same reviewed revision for deploy. |

**Procedure.** In the issue, identify the affected guide and the observable change. The worker updates that guide where it owns the interface; the orchestrator resolves cross-guide changes. Review examples against the actual CLI/help, schema and rendered UI. Replace obsolete commands rather than maintaining parallel instructions. Use reserved example domains and synthetic identifiers. Link to the canonical guide rather than recopying its flags into prompts.

**Release gate.** The PR handoff names the revised instructions, the exact synthetic scenario exercised, remaining private acceptance gaps and rollback or revocation if authority changed. If instructions are unaffected, say why. Never paste hostnames, machine inventory, real certificates, credential locations, production captures or secret-bearing output into public issues. A restart should need only the named Project, its linked issues/PRs and the maintained guides—not a previous chat transcript.
