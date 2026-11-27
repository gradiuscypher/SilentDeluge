# SilentDeluge

SilentDeluge distinguishes what an operator can see from what they have chosen to track.

## Language

**Sighting**:
A record that a particular source observed a device or system at a particular time. Sightings are visible whether or not the operator has opted to track the subject.

**Tracked asset**:
A device or system the operator has explicitly opted to follow over time. It remains a tracked asset between sightings; tracking alone grants no permission to inspect or modify it.

**Passive monitoring**:
Using evidence an authorized source already has to observe, investigate, or alert on a subject, even an untracked one. Asking the source to contact the subject for fresh evidence is a probe, not passive monitoring.

**Probe**:
An attempt to learn about a subject by contacting it directly or through another system. Probing requires explicit opt-in for the subject and the inspection being performed.

**Standing opt-in rule**:
An operator's explicit choice to grant a specified monitoring permission to assets that meet stated criteria, including future matches if selected. Individual opt-in is the default; discovery alone never grants permission.

**Effective monitoring permission**:
Permission for a particular collection on an asset while at least one individual opt-in or matching standing rule authorizes it and no explicit exclusion covers it. When permission ends, that collection stops; the asset's existing history is retained.

**Replacement**:
A new asset that takes over a previous asset's role. The replacement is linked to its predecessor, but has its own history and inherits no individual opt-in; a standing rule may cover it independently.

**Service**:
A capability that can be tracked independently of the hosts providing it. Its history continues across host moves; permission to check its endpoint does not grant permission to inspect its hosts.

**Placement**:
The relationship between a service and a host on which it ran during a particular period. A move changes placement without changing the service's identity or erasing its earlier placements.

**Declaration**:
A sourced, dated statement of how an operator or configuration describes an asset. Declarations remain distinct from observations throughout inventory.

**Observation**:
A sourced, dated record of what was actually seen. An observation does not overwrite a declaration, even when the two disagree.

**Inventory discrepancy**:
A flagged disagreement between declared and observed information about the same subject. The flag preserves both claims for investigation rather than deciding which one to discard.

**Observed absence**:
Evidence that a subject was not present in a particular scope at the time of a successful, complete check of that scope. A failed or partial check cannot establish absence or replace the last observation.

**Membership freshness**:
The time for which verified membership can support a standing opt-in rule. When that evidence goes stale, collection relying solely on the rule pauses until membership is verified again; an individual opt-in for the same VM and collection remains independent.

**Explicit exclusion**:
An operator's prohibition on a specified collection for a specified asset. It overrides every grant in that scope until removed, without affecting other collections.

**Collection pack**:
A set of monitors an operator can enable together or select from individually, down to a single metric. Enabling the whole pack includes checks added in later releases; explicit exclusions still win.

**Monitoring dependency**:
A required or optional collection input for an alert or detection; a dependency is not a collection grant. Before disabling collection, the operator sees the impact on each affected asset and may proceed regardless.
If another authorized source supplies the input, coverage continues. Otherwise, a missing required input leaves the dependent rule not evaluated (not healthy); a missing optional input removes only the context it supplied.

**AI provider route**:
An operator's permission to send evidence from a specified collection pack to a specified AI provider, hosted or local. A collection grant does not establish a route, and a route does not authorize collection.

**Derived evidence**:
A finding, summary, or report produced from other evidence. It may go only to providers authorized for every underlying input; summarizing it again does not widen that permission.

**Interactive agent**:
An agent acting for a signed-in user, limited to what both the user and the agent may access. Evidence is filtered before it reaches the model, including evidence used for counts and summaries.

**Scheduled agent**:
An agent acting under its own explicitly scoped identity for unattended work. It does not inherit the permissions of the operator who configured it.

**Monitoring goal**:
An operator's instruction about what an agent should watch, investigate, and report within its existing permissions. A goal is advisory by default: it can lead to a recommendation, but it does not authorize changing the monitored system.

**Remediation grant**:
A separate, explicit opt-in authorizing an agent to change a monitored system within a stated scope. Neither a monitoring goal nor a collection grant implies one.

**Finding**:
A record of a distinct condition affecting a subject. Repeated observations update the same open finding until a successful check verifies resolution; a failed check leaves it open with its last verification time.

**Finding reminder**:
A notification about an already-open finding, scheduled by the operator independently of scan frequency. Sending a reminder does not create another finding.

**Historical detection run**:
An evaluation of a specified rule version over retained, authorized evidence in a chosen time range. Matches carry their original event times and have no effect on live findings or notifications; an operator may explicitly promote a match to a finding.

**Log health**:
The status of incoming log streams relative to what the operator expects to receive. When expected logs stop arriving, the interruption is surfaced separately from detection results rather than repeated on individual matches.

**Log health expectation**:
A pack-defined signal and delivery window used to assess a feed, overridable per source or stream. A missed expected signal raises a log-health finding; without an expected signal, report last receipt rather than an outage. A collector heartbeat establishes reachability, not completeness of event delivery.

**Shared event field**:
A field with the same defined meaning across event sources, available to both detections and interactive queries. If the source does not establish its value, the value is unknown.

**Source-specific event field**:
A field interpreted according to its source's own event semantics. Detections and queries can use it alongside shared fields without requiring a cross-source equivalent.

**Retained payload**:
Source event content kept in addition to parsed fields, only when explicitly enabled for that stream. Exclusions apply before content is written to either storage tier; moving an event between tiers never restores excluded data.

**Active log storage**:
The SSD-backed tier for logs kept readily available for routine queries and detections.

**Cold log storage**:
The HDD-backed tier for retaining logs without consuming active-tier SSD capacity. Both queries and historical detection runs can span active and cold logs without a restore; the requested time range is not silently shortened to the active tier.

**Log retention policy**:
The time a stream's logs remain in active storage and the total time they are retained across both tiers, each measured from ingestion. The operator sets installation-wide defaults and can override them per stream; moving a log to cold storage does not restart either clock.

**Evidence hold**:
An explicit, time-limited exception to normal expiry for selected evidence. A finding does not create a hold: its rule, subject, timestamps, and status remain after source evidence expires, and its evidence reference shows that expiry rather than retaining a payload copy.

**Shared dashboard**:
A reusable layout and set of queries, not a copy of the author's results or access grants. Every panel, aggregate, and drill-down reflects only evidence the current viewer may see.

**Investigation report**:
A dated SilentDeluge record of conclusions and evidence references, readable in its authenticated UI without any external publisher. Pages is an optional publishing destination, not the authoritative copy; if the destination cannot enforce the report's access restrictions, share an authenticated report link instead.

**Notification destination**:
A specific delivery target, such as one Discord channel, with its own permissions for alerts and evidence streams. Operators can enable multiple alerts or streams in bulk for that destination without granting access to sibling channels.

**Alert pack subscription**:
A destination's ongoing selection of an alert pack. Changes to the pack, including new alerts and settings, apply automatically without another opt-in; delivery remains subject to the destination's evidence permissions.

**Local override**:
A scoped change to a pack setting that survives compatible upstream updates without freezing the rest of the pack. An incompatible override is surfaced as a conflict that an agent can inspect and resolve with appropriate permissions.

**Report link**:
A reference to a report, not a grant to read it. Opening it requires sign-in and permission to read the underlying evidence; its notification text and link preview must respect the destination's permissions.
