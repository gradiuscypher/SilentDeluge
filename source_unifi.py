"""Passive inventory from one UniFi Network site's local Integration API."""

from contextlib import closing
from datetime import datetime, timezone
from http.client import HTTPException, HTTPSConnection
import json
from math import isfinite
import os
import re
import ssl
from urllib.parse import urlsplit


_LIMIT = 200
_MAX_PAGES = 100
_TIMEOUT = 10
_ENRICHMENT_DEVICES = 16
_ENRICHMENT_BYTES = 256 * 1024
_UNAVAILABLE = (HTTPException, OSError, ValueError, RecursionError)
_ORIGIN = re.compile(r"https://(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:.]+\])(?::[0-9]+)?/?")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_COMMON_FIELDS = (("name", "name"), ("macAddress", "mac_address"), ("ipAddress", "ip_address"))
_LISTS = (
    ("managed_devices", "devices", _COMMON_FIELDS + (("model", "model"), ("state", "state"))),
    ("connected_clients", "clients", _COMMON_FIELDS + (("type", "type"),)),
)


def _uuid_or_none(value):
    return value.lower() if isinstance(value, str) and _UUID.fullmatch(value) else None


def _uuid(value):
    if (ident := _uuid_or_none(value)) is None:
        raise ValueError("expected a UniFi UUID")
    return ident


def _settings(config):
    origin = config.get("base_url")
    if not isinstance(origin, str) or not _ORIGIN.fullmatch(origin):
        raise ValueError("expected a local HTTPS origin")
    url = urlsplit(origin)
    host, port = url.hostname, url.port
    if port == 0 or host.rstrip(".") in ("api.ui.com", "unifi.ui.com"):
        raise ValueError("expected a local HTTPS origin")
    site = _uuid(config.get("site_id"))
    key_env = config.get("api_key_env")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("expected an API key environment variable name")
    key = os.environ.get(key_env, "")
    if not re.fullmatch(r"[\x21-\x7e]+", key):
        raise ValueError("missing or invalid UniFi API key")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    if "ca_file" in config:
        ca_file = config["ca_file"]
        if not isinstance(ca_file, str) or not ca_file:
            raise ValueError("expected a CA file path")
        context.load_verify_locations(cafile=ca_file)
    return host, port if port is not None else 443, site, key, context


def _get_json(host, port, context, key, path, *, max_bytes=None):
    # Direct HTTPS: no environment proxy and no interpretation of Location headers.
    with closing(HTTPSConnection(host, port, timeout=_TIMEOUT, context=context)) as conn:
        conn.request("GET", path, headers={"Accept": "application/json", "X-API-Key": key})
        with conn.getresponse() as response:
            if response.status != 200:
                raise ValueError("UniFi read did not succeed")
            if max_bytes is None:
                return json.loads(response.read())
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes or response.length not in (None, 0):
                raise ValueError("invalid UniFi response size")
            return json.loads(body)


def _page(raw, offset, expected_total):
    if not isinstance(raw, dict):
        raise ValueError("invalid UniFi page")
    page_offset, limit, count, total = (raw.get(key) for key in ("offset", "limit", "count", "totalCount"))
    data = raw.get("data")
    if (any(type(n) is not int for n in (page_offset, limit, count, total))
            or page_offset != offset or not 1 <= limit <= _LIMIT
            or not isinstance(data, list) or count != len(data) or count > limit
            or offset + count > total or (count == 0 and offset < total)
            or (expected_total is not None and total != expected_total)):
        raise ValueError("inconsistent UniFi pagination")
    return data, count, total


def _timestamp(value):
    if isinstance(value, str):
        try:
            if datetime.fromisoformat(value).utcoffset() is not None:
                return value
        except ValueError:
            pass
    return None


def _choices(raw, allowed):
    if isinstance(raw, list):
        known = list(dict.fromkeys(value for value in raw if value in allowed))
        if known or not raw:
            return known
    return None


def _device_overview(record, raw):
    for source, target in (("supported", "supported"), ("firmwareUpdatable", "firmware_updatable")):
        if type(value := raw.get(source)) is bool:
            record[target] = value
    if isinstance(version := raw.get("firmwareVersion"), str) and version:
        record["firmware_version"] = version
    for key, allowed in (("features", ("switching", "accessPoint", "gateway")),
                         ("interfaces", ("ports", "radios"))):
        if (value := _choices(raw.get(key), allowed)) is not None:
            record[key] = value


def _client_overview(record, raw):
    if (connected := _timestamp(raw.get("connectedAt"))) is not None:
        record["connected_at"] = connected
    local = record.get("type") in ("WIRED", "WIRELESS")
    if local and (uplink := _uuid_or_none(raw.get("uplinkDeviceId"))) is not None:
        record["uplink_device_id"] = uplink
    if isinstance(access := raw.get("access"), dict):
        kind = access.get("type")
        if kind == "DEFAULT" or (local and kind == "GUEST"):
            record["access_type"] = kind
        if local and kind == "GUEST" and type(authorized := access.get("authorized")) is bool:
            record["guest_authorized"] = authorized


def _fields(raw, specs):
    if not isinstance(raw, dict):
        return {}
    return {target: value for source, target, valid in specs
            if valid(value := raw.get(source))}


def _uint(value):
    return type(value) is int and 0 <= value < 2**63


def _positive_int(value):
    return _uint(value) and value > 0


def _nonnegative_number(value):
    return _uint(value) if type(value) is int else type(value) is float and isfinite(value) and value >= 0


def _percent(value):
    return _nonnegative_number(value) and value <= 100


def _band(value):
    return value in ("2.4", "5", "6", "60")


def _port(raw):
    port = _fields(raw, (
        ("idx", "index", _positive_int),
        ("state", "state", lambda v: v in ("UP", "DOWN", "UNKNOWN")),
        ("connector", "connector", lambda v: v in ("RJ45", "SFP", "SFPPLUS", "SFP28", "QSFP28")),
        ("maxSpeedMbps", "max_speed_mbps", _uint),
        ("speedMbps", "speed_mbps", _uint),
    ))
    if "index" not in port:
        return {}
    poe = _fields(raw.get("poe"), (
        ("standard", "standard", lambda v: v in ("802.3af", "802.3at", "802.3bt")),
        ("enabled", "enabled", lambda v: type(v) is bool),
        ("state", "state", lambda v: v in ("UP", "DOWN", "LIMITED", "UNKNOWN")),
    ))
    if poe:
        port["poe"] = poe
    return port


def _radio(raw):
    radio = _fields(raw, (
        ("frequencyGHz", "frequency_ghz", _band),
        ("wlanStandard", "wlan_standard",
         lambda v: v in ("802.11a", "802.11b", "802.11g", "802.11n", "802.11ac", "802.11ax", "802.11be")),
        ("channel", "channel", _positive_int),
        ("channelWidthMHz", "channel_width_mhz", _positive_int),
    ))
    return radio if "frequency_ghz" in radio else {}


def _interface_rows(raw, key, project):
    interfaces = raw.get("interfaces")
    rows = interfaces.get(key) if isinstance(interfaces, dict) else None
    return [row for item in rows if (row := project(item))] if isinstance(rows, list) else []


def _details(raw):
    details = {}
    for source, target in (("adoptedAt", "adopted_at"), ("provisionedAt", "provisioned_at")):
        if (value := _timestamp(raw.get(source))) is not None:
            details[target] = value
    if isinstance(uplink := raw.get("uplink"), dict):
        if (parent := _uuid_or_none(uplink.get("deviceId"))) is not None:
            details["uplink_device_id"] = parent
    for key, project in (("ports", _port), ("radios", _radio)):
        if rows := _interface_rows(raw, key, project):
            details[key] = rows
    return details


def _radio_statistics(raw):
    radio = _fields(raw, (("frequencyGHz", "frequency_ghz", _band),
                          ("txRetriesPct", "tx_retries_pct", _percent)))
    return radio if len(radio) == 2 else {}


def _statistics(raw):
    stats = _fields(raw, (
        ("uptimeSec", "uptime_seconds", _uint),
        ("loadAverage1Min", "load_average_1_min", _nonnegative_number),
        ("loadAverage5Min", "load_average_5_min", _nonnegative_number),
        ("loadAverage15Min", "load_average_15_min", _nonnegative_number),
        ("cpuUtilizationPct", "cpu_utilization_pct", _percent),
        ("memoryUtilizationPct", "memory_utilization_pct", _percent),
    ))
    if (heartbeat := _timestamp(raw.get("lastHeartbeatAt"))) is not None:
        stats["last_heartbeat_at"] = heartbeat
    if radios := _interface_rows(raw, "radios", _radio_statistics):
        stats["radios"] = radios
    return stats


def _record(raw, prefix, fields):
    if not isinstance(raw, dict):
        raise ValueError("invalid UniFi record")
    ident = f"{prefix}:{_uuid(raw.get('id'))}"
    record = {"id": ident}
    for source, target in fields:
        value = raw.get(source)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError("invalid UniFi record field")
            record[target] = value
    return ident, record


def _collect_list(settings, section, endpoint, fields):
    host, port, site, key, context = settings
    path = f"/proxy/network/integration/v1/sites/{site}/{endpoint}"
    prefix = f"unifi:{site}:{section}"
    records = {}
    project = _device_overview if section == "managed_devices" else _client_overview
    offset, total = 0, None
    usable = True
    for _ in range(_MAX_PAGES):
        try:
            raw = _get_json(host, port, context, key, f"{path}?offset={offset}&limit={_LIMIT}")
            data, count, total = _page(raw, offset, total)
        except _UNAVAILABLE:
            break
        for item in data:
            try:
                ident, record = _record(item, prefix, fields)
            except ValueError:
                usable = False
                continue
            if ident in records:
                usable = False
            else:
                project(record, item)
                records[ident] = record
        offset += count
        if offset == total and usable:
            return {"section": section, "outcome": "complete", "records": list(records.values())}
        if offset == total:
            break
    return {"section": section, "outcome": "partial" if records else "failed",
            "records": list(records.values())}


def _enrich_devices(config, settings, records):
    reads = []
    if config.get("device_details") is True:
        reads.append(("details", "", _details))
    if config.get("device_statistics") is True:
        reads.append(("statistics", "/statistics/latest", _statistics))
    if not reads:
        return
    host, port, site, key, context = settings
    for record in records[:_ENRICHMENT_DEVICES]:
        device_id = record["id"].rsplit(":", 1)[1]
        path = f"/proxy/network/integration/v1/sites/{site}/devices/{device_id}"
        for field, suffix, project in reads:
            try:
                raw = _get_json(host, port, context, key, path + suffix, max_bytes=_ENRICHMENT_BYTES)
            except _UNAVAILABLE:
                continue
            if not isinstance(raw, dict) or (field == "details" and _uuid_or_none(raw.get("id")) != device_id):
                continue
            if evidence := project(raw):
                evidence["observed_at"] = datetime.now(timezone.utc).isoformat()
                record[field] = evidence


def collect(config):
    """Return independent section attempts; never treat disconnected clients as absent."""
    if config.get("enabled") is True:
        try:
            settings = _settings(config)
        except (OSError, ValueError):
            pass
        else:
            sections = [_collect_list(settings, *listing) for listing in _LISTS]
            _enrich_devices(config, settings, sections[0]["records"])
            return sections
    return [{"section": section, "outcome": "failed", "records": []} for section, _, _ in _LISTS]
