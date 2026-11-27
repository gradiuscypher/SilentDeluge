# UniFi Network sightings

This opt-in source reads the [local UniFi Network Integration API v1](https://developer.ui.com/network/v10.0.162/openapi.json), using the Network **10.0.162** schema as its documented compatibility target. Check **UniFi Network → Integrations** on the installed version for its [version-specific API reference](https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API). Other versions require private verification against the same contract. The collector does not look up sites, query the Site Manager or cloud connector, fall back to legacy endpoints, contact the reported devices, or make changes.

The base reads are these unfiltered `GET`s, with `offset` and `limit`, on one configured console and site:

| Section | Route under `/proxy/network/integration` | Meaning |
| --- | --- | --- |
| `managed_devices` | [`/v1/sites/{siteId}/devices`](https://developer.ui.com/network/v10.0.162/getadopteddeviceoverviewpage) | Adopted infrastructure, including devices the controller reports offline. |
| `connected_clients` | [`/v1/sites/{siteId}/clients`](https://developer.ui.com/network/v10.0.162/getconnectedclientoverviewpage) | Clients the controller reports connected, including VPN connections. |

Two optional, non-paginated `GET`s can enrich sightings of adopted devices already returned by that site's list. They go to the same configured console, never to the devices themselves:

| Config flag | Route under `/proxy/network/integration` |
| --- | --- |
| `device_details` | [`/v1/sites/{siteId}/devices/{deviceId}`](https://developer.ui.com/network/v10.0.162/getadopteddevicedetails) |
| `device_statistics` | [`/v1/sites/{siteId}/devices/{deviceId}/statistics/latest`](https://developer.ui.com/network/v10.0.162/getadopteddevicelateststatistics) |

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
- The optional flags are independent and off unless set to JSON `true`. Enable only an endpoint the operator has approved and the existing key can read without broader permissions. After both list attempts, the collector tries each enabled read once for the first 16 unique valid managed-device sightings in API order, including offline sightings. Optional reads are serial, use the same 10-second socket inactivity timeout, and accept at most 256 KiB of response body each. This caps the extra requests at 32, not the total elapsed time. There are no retries or client detail reads.

The [collection runner](COLLECTION.md) takes no declarations and writes `source`, `scope`, and a UTC collection time around the sections:

```sh
python3 collection.py unifi --config "$PRIVATE/unifi.json"
```

## Interpret the evidence

- Each record's `id` is `unifi:<site UUID>:<section>:<integration API record UUID>`. Use it within the envelope's `source` and `scope`. Names and addresses are descriptive, not identity keys. Do not merge devices and clients by MAC/IP or assume that a reissued client or VPN connection UUID represents the same subject.
- Both lists retain available `name`, `mac_address`, and `ip_address` alongside `id`. `managed_devices` adds `model`, connectivity `state`, controller-reported `supported`, `firmware_version`, `firmware_updatable`, and known `features` (`switching`, `accessPoint`, `gateway`) and `interfaces` (`ports`, `radios`). These are not a vendor support-lifecycle audit or an independent update check.
- `connected_clients` adds `type` and `connected_at`. For wired and wireless clients it can report `uplink_device_id` (the switch/AP UUID, not a port or SSID). `access_type` is `DEFAULT` or, for local guest clients, `GUEST`; `guest_authorized` is included only for a guest when the API supplies a boolean. `DEFAULT` does not prove a security policy or authentication state. VPN and Teleport may have no MAC address or local attachment. An uplink UUID is a controller-reported reference, not a new sighting or verification of a physical path.
- Missing or unusable enrichment fields are omitted independently; they do not turn a usable row into a failed sighting. `false` and numeric zero are retained when valid. Overview capability arrays can be explicitly empty, and only documented members of nonempty arrays are retained. No raw payloads, credentials, configuration IDs, guest voucher data or packet content are stored. Identifiers, names, addresses, topology and connection times are private inventory.
- Outcomes are independent. `complete` means pagination reached a consistent end of that list, including a valid empty list. `partial` retains positive sightings when enumeration is incomplete; `failed` means no usable sightings from an incomplete attempt. An invalid/disabled setup produces two `failed` sections without making requests. Read the outcomes, not the runner's exit status alone.
- The collector requests up to 200 rows per page, advances by the validated returned count, and reads at most 100 pages per section with a 10-second socket timeout. Pagination mismatches, a changed total, transport errors and non-200 responses end the section. Invalid records and duplicate IDs prevent completeness; valid sightings are kept, using the first valid record for a duplicate. Offset pagination is not an atomic snapshot: substitutions during a changing list may be undetectable even when counts match.
- A `partial` or `failed` attempt cannot establish absence or erase prior verified evidence. Even after a `complete` client list, a missing client has **unknown** inventory presence: there is no disconnected-client or historical inventory in this source. An adopted device reported `OFFLINE` is still a managed-device sighting, not an omission.

### Optional per-device evidence

| Record object | Retained fields when available |
| --- | --- |
| `details` | `adopted_at`, `provisioned_at`, parent `uplink_device_id`; `ports` with `index`, `state`, `connector`, `max_speed_mbps`, `speed_mbps`, and PoE `standard`, `enabled`, `state`; `radios` with `frequency_ghz`, WLAN standard, channel and `channel_width_mhz`. |
| `statistics` | `uptime_seconds`, `last_heartbeat_at`, `load_average_1_min`, `load_average_5_min`, `load_average_15_min` (load averages, not percentages), CPU and memory utilization in percent, and radio `tx_retries_pct` by `frequency_ghz`. |

`_mbps` means megabits/s, `_mhz` megahertz, `_ghz` gigahertz (band label as a string), and `_pct` percentage on a 0–100 scale. Link speed is not measured throughput. Uplink transfer rates are excluded: the published 10.0.162 schema names them `txRateBps` and `rxRateBps` without stating whether the unit is bits or bytes per second.

Each emitted optional object has its own UTC `observed_at`, taken by the collector when it reads the response. It is not the device's sample time; the API supplies no separate measurement timestamp for these statistics. Reported adoption, provisioning, connection and last heartbeat times are preserved with their source offsets when present. Statistics are a last-reported snapshot that may be stale, especially for offline devices. Detail data never overrides fields from the overview.

Optional responses that are denied, unsupported, malformed, too large or (for details) return the wrong device UUID cannot change either base section's outcome. A missing optional object can also mean the read was disabled, the device was outside the request cap, or no usable fields were returned. It is not a negative observation.

## Private acceptance before scheduling

1. Record the installed Network version, approved console/site, approved reads, effective key policy and revocation procedure in the private installation. Verify TLS trust and whole-site visibility of both lists. Use policy inspection for denied capabilities, not a test mutation or a read against an unapproved site. Disable the source if scope or permissions cannot be verified.
2. With one-time operator approval, run the command once using the dedicated key. Keep the output private (for example, redirect to a restricted file). Check both lists against the same site's controller view at collection time; if available, compare an offline adopted device and a multi-page list. If enabling optional reads, approve them separately and compare available lifecycle, link and statistics fields on a few in-cap devices. Note unsupported fields, stale readings and anything not verified. Do not disconnect clients or modify devices to create a case.
3. Exercise the incomplete and empty-list cases with the synthetic tests, not by weakening TLS or changing the real controller. Verify downstream handling preserves prior evidence and makes no missing-client absence claim. Approve unattended scheduling separately. To stop, disable collection and revoke the dedicated key; retain evidence under the private retention policy.

The repository's synthetic acceptance check is `python3 -m unittest -v test_source_unifi`; it does not open sockets. It cannot verify installed-version field availability, live TLS, effective key permissions or controller freshness.
