# UniFi Network sightings

This opt-in source reads the [local UniFi Network Integration API v1](https://developer.ui.com/network/v10.0.162/openapi.json), using the Network **10.0.162** schema as its documented compatibility target. Check **UniFi Network → Integrations** on the installed version for its [version-specific API reference](https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API). Other versions require private verification against the same contract. The collector does not look up sites, query the Site Manager or cloud connector, fall back to legacy endpoints, contact the reported devices, or make changes.

The only requests are these unfiltered `GET`s, with `offset` and `limit`, on one configured console and site:

| Section | Route under `/proxy/network/integration` | Meaning |
| --- | --- | --- |
| `managed_devices` | [`/v1/sites/{siteId}/devices`](https://developer.ui.com/network/v10.0.162/getadopteddeviceoverviewpage) | Adopted infrastructure, including devices the controller reports offline. |
| `connected_clients` | [`/v1/sites/{siteId}/clients`](https://developer.ui.com/network/v10.0.162/getconnectedclientoverviewpage) | Clients the controller reports connected, including VPN connections. |

## Configure privately

After the operator approves passive reads of **both** lists for the named site, put this shape in a private JSON file. The values below are synthetic:

```json
{
  "scope": "theatre-console/stage",
  "enabled": true,
  "base_url": "https://theatre-console.example.test",
  "site_id": "10000000-0000-4000-8000-000000000001",
  "api_key_env": "SILENTDELUGE_UNIFI_API_KEY"
}
```

- Use a stable `scope` unique to the console/site and the exact site UUID from the operator-approved setup. There is no site-name lookup or default site. Omit `enabled` or set it to `false` to disable all requests. A sighting does not enroll a tracked asset or grant permission to probe it.
- `base_url` is the direct local console HTTPS origin (hostname and optional port, no API path, user info, query or fragment). The certificate must match the configured name or IP and chain to a trusted CA. For a private CA, set `ca_file` to the path of its PEM certificate bundle; it augments system trust. TLS verification is mandatory. Environment proxies and all redirects are ignored.
- Provision a dedicated Integration API key **only if** the installed version and policy can grant read-only access to the approved site with full visibility of both lists. Confirm the effective **key** permissions; read-only HTTP methods do not make a broad key read-only. If the version requires a write-capable, multi-site, or administrator key for these reads, leave the source disabled. Supply the dedicated key in the named process environment variable via the private secret store or service manager, not in JSON, shell history, logs or Git. The collector sends it in `X-API-Key` only to the configured origin over verified TLS.

The [collection runner](COLLECTION.md) takes no declarations and writes `source`, `scope`, and a UTC collection time around the sections:

```sh
python3 collection.py unifi --config "$PRIVATE/unifi.json"
```

## Interpret the evidence

- Each record's `id` is `unifi:<site UUID>:<section>:<integration API record UUID>`. Use it within the envelope's `source` and `scope`. Names and addresses are descriptive, not identity keys. Do not merge devices and clients by MAC/IP or assume that a reissued client or VPN connection UUID represents the same subject.
- Both sections retain only `id` and available `name`, `mac_address`, and `ip_address`; devices may also have `model` and `state`, clients `type`. Missing or null descriptive fields are omitted. No raw controller payloads are stored. Site identifiers, names and addresses are private inventory, even though keys are not included.
- Outcomes are independent. `complete` means pagination reached a consistent end of that list, including a valid empty list. `partial` retains positive sightings when enumeration is incomplete; `failed` means no usable sightings from an incomplete attempt. An invalid/disabled setup produces two `failed` sections without making requests. Read the outcomes, not the runner's exit status alone.
- The collector requests up to 200 rows per page, advances by the validated returned count, and reads at most 100 pages per section with a 10-second socket timeout. Pagination mismatches, a changed total, transport errors and non-200 responses end the section. Invalid records and duplicate IDs prevent completeness; valid sightings are kept, using the first valid record for a duplicate. Offset pagination is not an atomic snapshot: substitutions during a changing list may be undetectable even when counts match.
- A `partial` or `failed` attempt cannot establish absence or erase prior verified evidence. Even after a `complete` client list, a missing client has **unknown** inventory presence: there is no disconnected-client or historical inventory in this source. An adopted device reported `OFFLINE` is still a managed-device sighting, not an omission.

## Private acceptance before scheduling

1. Record the installed Network version, approved console/site, approved reads, effective key policy and revocation procedure in the private installation. Verify TLS trust and whole-site visibility of both lists. Use policy inspection for denied capabilities, not a test mutation or a read against an unapproved site. Disable the source if scope or permissions cannot be verified.
2. With one-time operator approval, run the command once using the dedicated key. Keep the output private (for example, redirect to a restricted file). Check both lists against the same site's controller view at collection time; if available, compare an offline adopted device and a multi-page list. Note anything not verified. Do not disconnect clients or modify devices to create a case.
3. Exercise the incomplete and empty-list cases with the synthetic tests, not by weakening TLS or changing the real controller. Verify downstream handling preserves prior evidence and makes no missing-client absence claim. Approve unattended scheduling separately. To stop, disable collection and revoke the dedicated key; retain evidence under the private retention policy.
