"""Passive inventory from one UniFi Network site's local Integration API."""

from contextlib import closing
from http.client import HTTPException, HTTPSConnection
import json
import os
import re
import ssl
from urllib.parse import urlsplit


_LIMIT = 200
_MAX_PAGES = 100
_TIMEOUT = 10
_ORIGIN = re.compile(r"https://(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:.]+\])(?::[0-9]+)?/?")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_COMMON_FIELDS = (("name", "name"), ("macAddress", "mac_address"), ("ipAddress", "ip_address"))
_LISTS = (
    ("managed_devices", "devices", _COMMON_FIELDS + (("model", "model"), ("state", "state"))),
    ("connected_clients", "clients", _COMMON_FIELDS + (("type", "type"),)),
)


def _uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise ValueError("expected a UniFi UUID")
    return value.lower()


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


def _get_page(host, port, context, key, path):
    # Direct HTTPS: no environment proxy and no interpretation of Location headers.
    with closing(HTTPSConnection(host, port, timeout=_TIMEOUT, context=context)) as conn:
        conn.request("GET", path, headers={"Accept": "application/json", "X-API-Key": key})
        with conn.getresponse() as response:
            if response.status != 200:
                raise ValueError("UniFi list request did not succeed")
            return json.loads(response.read())


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
    offset, total = 0, None
    usable = True
    for _ in range(_MAX_PAGES):
        try:
            raw = _get_page(host, port, context, key, f"{path}?offset={offset}&limit={_LIMIT}")
            data, count, total = _page(raw, offset, total)
        except (HTTPException, OSError, ValueError, RecursionError):
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
                records[ident] = record
        offset += count
        if offset == total and usable:
            return {"section": section, "outcome": "complete", "records": list(records.values())}
        if offset == total:
            break
    return {"section": section, "outcome": "partial" if records else "failed",
            "records": list(records.values())}


def collect(config):
    """Return independent section attempts; never treat disconnected clients as absent."""
    if config.get("enabled") is True:
        try:
            settings = _settings(config)
        except (OSError, ValueError):
            pass
        else:
            return [_collect_list(settings, *listing) for listing in _LISTS]
    return [{"section": section, "outcome": "failed", "records": []} for section, _, _ in _LISTS]
