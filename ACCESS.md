# Collector settings and access

The workspace is for the signed-in account. Use **Collectors** (`/collectors`) to review approved source IDs, providers, exact scopes and read descriptions. Use **Access** (`/access`) to review effective permissions on configured sources. Sources outside your view scopes and other accounts' settings are not listed. The workspace links back to the overview, Machines, Incus, UniFi, Relationships and the local design patterns.

## Current permissions

The server's private configuration is authoritative. There are no project memberships, groups, named roles or collector ACLs in the running authorization model.

| Setting | Effect |
| --- | --- |
| `view_scopes` | Grants visibility of allowlisted sources in an exact scope and permission to enable or disable your own evidence view. |
| Evidence view | Starts disabled for each account and source. Enabling it shows retained evidence and applicable declarations; disabling it hides both for you. Neither changes another account, a grant, or stored evidence. |
| `collect_scopes` | A separate subset of `view_scopes`. A grant permits preview and one manual collection only when your evidence view is enabled and the source is not import-only. `Denied` on a collectable source means this grant is absent. |
| `import_only: true` | Evidence is imported by an operator outside the workspace. Preview and manual collection are unavailable regardless of grants. |

Access groups visible sources by exact scope and lists each collector's evidence preference and collection permission. It is not an account or grant editor. A scope with no visible configured collector does not appear; an import-only row reports the operation as unavailable, not whether its scope has an otherwise unusable collection grant.

**Enable/Disable evidence view** changes only your preference through the existing `/enable` and `/disable` POSTs. **Preview one collection** uses `/preview`; it describes the configured read and destination without performing it. Confirmation is a separate `/collect` POST with a fresh, single-use preview token. A preview expires after five minutes. No action grants access, runs a probe or creates a schedule. Settings forms use the current account's CSRF token; collection permission and request checks remain server-side. **Log out** posts the token to `/logout`. Return links use fixed workspace locations rather than a caller-supplied URL.

## New collector guide

**New collector** (`/collectors/new`) lets you choose one of the three supported providers and preview a synthetic, standalone collection-runner template with read requirements:

- [Linux](LINUX.md): run unprivileged inside the enrolled guest, with only approved packs. Import remote guest evidence rather than running on the web server as a substitute.
- [Incus](INCUS-SETUP.md): the example reads a saved instance export with a matching scope; it makes no live Incus connection. Live export or metrics capture needs separate approval.
- [UniFi](UNIFI.md): the example is disabled and uses a reserved example hostname and an environment variable name, not a key. Require approved site-specific reads, a dedicated read-only key and verified TLS before enabling real collection privately.

The guide does not accept provider configuration or secrets, contact a provider, write a file, install a collector or grant permissions. The templates are not browser submissions or web allowlist entries. For a real setup, an operator approves exact reads and scope, configures the collector privately, and adds the source and grants separately under [WEB.md](WEB.md#private-configuration-and-permissions). Keep endpoints, credentials and evidence in the private installation.

## Proposed authorization model — not implemented

- **Project membership:** A project would own its collectors. Membership would be required before any project grant can apply, and would not itself confer access.
- **Group roles:** Project-local groups would bundle operations, such as viewer (`view`), operator (`view`, `preview`, `collect`), or access administrator (`manage access`). Managing access would not implicitly permit reading evidence or running collectors.
- **Per-collector ACL:** A grant would bind a project member or group to an exact collector and explicit operations. A role would supply operations, not automatically apply them to every collector. Preview and collect would require separate authorization checks; a preview would not preserve a revoked grant.
- **Exclusions:** Deny by default. A matching member or group exclusion would override every grant for that collector and operation. Excluding `view` would also prevent preview and collect. Evidence enablement and import-only restrictions would remain independent of ACLs.

For example, an operator group could be granted access to two collectors, while a member exclusion denies `collect` on one; the member could still see its evidence and, with the required grants and enabled view, preview it. None of these proposed rules apply in the running workspace.
