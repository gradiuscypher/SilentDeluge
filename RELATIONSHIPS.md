# Relationship evidence

`/relationships` uses only sources already in the signed-in account's view and with evidence enabled. Viewing an association grants no collection, inspection, or modification permission. Filters (`source`, `q`) narrow the display after checking conflicts across those visible sources.

## Operator placement and address candidates

- Set a Linux source's `placement` to `{"scope":"theatre/stage","name":"tin-piano"}` only after approving that placement. The scope and name are exact, case-sensitive Incus identifiers. The view confirms the placement against a positive row in the latest Incus instance capture and shows its date. Declarations, a similar name, or retained rows from superseded captures are not confirmation. Confirmation is as of the evidence, not a live existence check.
- Set the same explicit `network_domain` on both sources only when their address evidence belongs to the same addressing domain. Use different identifiers for independently routed or overlapping address spaces. Leave it unset when the address scope is uncertain or a source mixes address domains; no matches are inferred from the site, project, scope name, or address prefix.
- Candidates compare Linux `addresses` (`asset`, `ip_address`), Incus `guest_addresses` (`name`, `ip_address`), and UniFi `managed_devices` and `connected_clients` (`id`, `ip_address`). Both captures must be current for their respective sections, no older than one hour, no later than the render time, and at most five minutes apart. The view compares canonical single IPs, normalizing IPv4-mapped IPv6 to IPv4. It excludes scoped literals, prefixes, unspecified, loopback, link-local, multicast, and the IPv4 limited broadcast address.
- Several subjects on the same side, or the same IP reported by multiple sites in the same domain, suppress the candidate. Retained conflicting sightings also suppress it; they cannot create one. If Linux and Incus both match a UniFi sighting, they must have an independently confirmed exact placement to avoid competing guest subjects. An IP overlap is never proof of identity or ownership. The view cannot rule out conflicts outside the authorized evidence or reuse not present in the retained captures.

## Linux guest addresses: opt in

Add `addresses` to the selected Linux `packs` only when disclosure of local IP addresses is approved. The collector invokes the fixed iproute2 command `ip -j address show` unprivileged in its current network namespace. It captures individual assigned IPv4 and IPv6 addresses, skipping tentative and duplicate-address-detection failures. Only the configured asset label and deduplicated canonical IPs enter the section. No routes, neighbors, MACs, probes, DNS lookups, or other namespace access. If iproute2 with JSON output is absent, denied, or malformed, the section fails rather than claiming an empty snapshot. Existing packs and defaults do not read addresses.

## Incus guest addresses: operator opt in

The existing whole-project operator export remains unchanged by default: it does not read guest state. To include addresses, choose one of two mutually exclusive options on the approved export command in [Incus setup](INCUS-SETUP.md):

- Repeat `--guest-address-instance NAME` for each explicitly approved guest, for example `--guest-address-instance tin-piano --guest-address-instance trapdoor`. After reading the whole-project inventory, the exporter checks the entire selection before reading any guest state. Every name must be exact, case-sensitive, unique, and listed as running in the approved scope. An invalid selection aborts without replacing the destination.
- Use `--guest-addresses-state PATH` to import a private, operator-controlled file containing the fixed projection below. This option makes no additional Incus calls. The operator attests that the rows came from approved runtime interface state in the exact `server/project` and that the timestamp dates that capture. Do not supply instance definitions, NIC configuration, DHCP reservations, or raw API output.

With explicit names, the exporter uses the selected remote and project to read only `GET /1.0/instances/<encoded-name>/state?project=<encoded-project>` for each selected guest. It does not run guest commands, perform recursive state enumeration, or add other query filters. A successful response must report a running guest and contain network metadata. Only valid, unscoped single IPs matching their reported IPv4 or IPv6 family are kept; invalid entries are skipped. The address timestamp dates the start of the state reads, separately from the inventory timestamp. Both options use the same projection:

```json
{
  "source": "incus-instance-state",
  "scope": "theatre/stage",
  "timestamp": "2025-01-01T10:05:00Z",
  "records": [{"name": "tin-piano", "ip_address": "192.0.2.7"}]
}
```

For explicit reads, a denied, failed or malformed response, missing network metadata, a guest no longer running, or a 30-second read timeout leaves that guest without new address sightings. The exporter warns with a count, not response contents, and keeps any valid sightings from other selected guests. If no valid addresses were obtained, it omits the address block. These state failures do not downgrade or discard a valid complete inventory. Both options import as a separately dated, partial `guest_addresses` section when the block is present. Neither omission nor a partial result proves that a guest or address is absent.

Review and transfer only the private snapshot. No raw guest state, interface names, MACs, counters, or other state fields are written. Use existing, explicitly approved operator-side read access for the selected guests. Do not grant the offline collector credentials, group membership, routes, or live access.
