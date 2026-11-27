"""UniFi collector tests over synthetic HTTP responses; no socket is opened."""

from datetime import datetime, timedelta
from http.client import HTTPSConnection
from io import BytesIO
import json
import os
import ssl
import unittest
from unittest.mock import patch

from source_unifi import collect


SITE = "10000000-0000-4000-8000-000000000001"
DEVICE = "20000000-0000-4000-8000-00000000000a"
OTHER_DEVICE = "20000000-0000-4000-8000-00000000000b"
VPN = "30000000-0000-4000-8000-00000000000c"
ORIGIN = "https://console.example.test:8443"
KEY_ENV = "SILENTDELUGE_TEST_UNIFI_KEY"
KEY = "synthetic-credential"
CONFIG = {"enabled": True, "base_url": ORIGIN, "site_id": SITE, "api_key_env": KEY_ENV}


def page(*rows, offset=0, total=None, **extra):
    return {"offset": offset, "limit": 200, "count": len(rows),
            "totalCount": len(rows) if total is None else total, "data": list(rows), **extra}


def response(payload, status=200, headers=()):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return (f"HTTP/1.1 {status} Synthetic\r\nContent-Length: {len(body)}\r\n".encode()
            + b"".join(header.encode() + b"\r\n" for header in headers) + b"\r\n" + body)


def get(resource, offset=0):
    return (f"GET /proxy/network/integration/v1/sites/{SITE}/{resource}"
            f"?offset={offset}&limit=200 HTTP/1.1").encode()


def get_device(device, suffix=""):
    return (f"GET /proxy/network/integration/v1/sites/{SITE}/devices/{device}{suffix} HTTP/1.1").encode()


class Wire:
    def __init__(self, request_log, answer):
        self.request_log = request_log
        self.answer = answer

    def sendall(self, data):
        self.request_log.append(data)

    def makefile(self, mode):
        return BytesIO(self.answer)

    def close(self):
        pass


class UniFiTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {KEY_ENV: KEY, "HTTPS_PROXY": "http://proxy.invalid"},
                                     clear=True))

    def capture(self, *answers, config=CONFIG, origin=("console.example.test", 8443)):
        pending = iter(answers)
        requests, origins = [], []

        def connection(host, port, *, context, timeout):
            origins.append((host, port))
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            conn = HTTPSConnection(host, port, context=context, timeout=timeout)
            answer = next(pending)

            def connect():
                if isinstance(answer, Exception):
                    raise answer
                conn.sock = Wire(requests, answer)

            conn.connect = connect
            return conn

        with patch("source_unifi.HTTPSConnection", side_effect=connection):
            sections = collect(config)
        self.assertEqual(len(origins), len(answers))
        self.assertEqual(origins, [origin] * len(answers))
        return sections, requests

    def test_paginates_only_the_configured_site_and_projects_inventory_fields(self):
        sections, requests = self.capture(
            response(page({"id": DEVICE.upper(), "name": "Stage switch", "model": "Switch",
                           "state": "OFFLINE", "macAddress": "02:00:00:00:00:01",
                           "ipAddress": "192.0.2.1", "firmwareVersion": "6.6.55", "supported": False,
                           "firmwareUpdatable": True, "features": ["switching", {"internal": KEY}],
                           "interfaces": ["ports"], "serialNumber": KEY}, total=2,
                          next="https://elsewhere.invalid/")),
            response(page({"id": OTHER_DEVICE, "name": "Lobby AP", "state": "ONLINE"}, offset=1, total=2)),
            response(page({"id": DEVICE, "name": "Ticket booth", "type": "WIRELESS",
                           "macAddress": "02:00:00:00:00:02", "uplinkDeviceId": OTHER_DEVICE.upper(),
                           "connectedAt": "2026-10-01T10:00:00+00:00",
                           "access": {"type": "GUEST", "authorized": False, "voucher": KEY}}, total=2)),
            response(page({"id": VPN, "name": "Remote crew", "type": "VPN", "macAddress": None,
                           "uplinkDeviceId": OTHER_DEVICE, "access": {"type": "DEFAULT", "authorized": True}},
                          offset=1, total=2)),
        )
        self.assertEqual([request.split(b"\r\n", 1)[0] for request in requests],
                         [get("devices"), get("devices", 1), get("clients"), get("clients", 1)])
        for request in requests:
            self.assertIn(b"\r\nHost: console.example.test:8443\r\n", request)
            self.assertIn(b"\r\nX-API-Key: synthetic-credential\r\n", request)
        self.assertEqual(sections, [
            {"section": "managed_devices", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:managed_devices:{DEVICE}", "name": "Stage switch", "model": "Switch",
                 "state": "OFFLINE", "mac_address": "02:00:00:00:00:01", "ip_address": "192.0.2.1",
                 "firmware_version": "6.6.55", "supported": False, "firmware_updatable": True,
                 "features": ["switching"], "interfaces": ["ports"]},
                {"id": f"unifi:{SITE}:managed_devices:{OTHER_DEVICE}", "name": "Lobby AP", "state": "ONLINE"},
            ]},
            {"section": "connected_clients", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:connected_clients:{DEVICE}", "name": "Ticket booth", "type": "WIRELESS",
                 "mac_address": "02:00:00:00:00:02", "uplink_device_id": OTHER_DEVICE,
                 "connected_at": "2026-10-01T10:00:00+00:00", "access_type": "GUEST", "guest_authorized": False},
                {"id": f"unifi:{SITE}:connected_clients:{VPN}", "name": "Remote crew", "type": "VPN",
                 "access_type": "DEFAULT"},
            ]},
        ])

    def test_bad_optional_overview_fields_do_not_lose_sightings_or_enable_reads(self):
        sections, _ = self.capture(
            response(page({"id": DEVICE, "name": "Stage switch", "supported": 1, "firmwareUpdatable": None,
                           "firmwareVersion": {"private": KEY}, "features": [KEY], "interfaces": []})),
            response(page({"id": VPN, "type": "WIRED", "connectedAt": "2026-10-01",
                           "uplinkDeviceId": "../clients", "access": {"type": "GUEST", "authorized": 0}})),
            config={**CONFIG, "device_details": "true", "device_statistics": 1},
        )
        self.assertEqual(sections, [
            {"section": "managed_devices", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:managed_devices:{DEVICE}", "name": "Stage switch", "interfaces": []},
            ]},
            {"section": "connected_clients", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:connected_clients:{VPN}", "type": "WIRED", "access_type": "GUEST"},
            ]},
        ])

    def test_optional_device_reads_project_lifecycle_link_and_timestamped_metrics(self):
        adopted = "2026-01-01T01:00:00+01:00"
        provisioned = "2026-10-01T10:00:00Z"
        heartbeat = "2026-10-01T11:00:00Z"
        sections, requests = self.capture(
            response(page({"id": DEVICE.upper(), "state": "OFFLINE", "firmwareVersion": "6.6.55"})),
            response(page()),
            response({"id": DEVICE.upper(), "state": "ONLINE", "firmwareVersion": "6.7.0",
                      "adoptedAt": adopted, "provisionedAt": provisioned, "configurationId": KEY,
                      "uplink": {"deviceId": OTHER_DEVICE.upper(), "private": KEY},
                      "interfaces": {
                          "ports": [{"idx": 1, "state": "UP", "connector": "RJ45", "maxSpeedMbps": 2500,
                                     "speedMbps": 1000, "poe": {"standard": "802.3at", "enabled": False,
                                                               "state": "DOWN", "ignored": KEY}},
                                    {"idx": 2, "state": "DOWN", "speedMbps": None}, {"idx": False}],
                          "radios": [{"frequencyGHz": "5", "wlanStandard": "802.11ax", "channel": 36,
                                      "channelWidthMHz": 40, "private": KEY}],
                      }}),
            response({"uptimeSec": 0, "lastHeartbeatAt": heartbeat, "nextHeartbeatAt": provisioned,
                      "loadAverage1Min": 0.125, "loadAverage5Min": None, "loadAverage15Min": 1.5,
                      "cpuUtilizationPct": 0, "memoryUtilizationPct": 12.5,
                      "uplink": {"txRateBps": 1024, "rxRateBps": 4096},
                      "interfaces": {"radios": [{"frequencyGHz": "5", "txRetriesPct": 2.25},
                                                 {"frequencyGHz": "6", "txRetriesPct": None}]}}),
            config={**CONFIG, "device_details": True, "device_statistics": True},
        )
        self.assertEqual([request.split(b"\r\n", 1)[0] for request in requests], [
            get("devices"), get("clients"), get_device(DEVICE), get_device(DEVICE, "/statistics/latest"),
        ])
        self.assertEqual([section["outcome"] for section in sections], ["complete", "complete"])
        record, = sections[0]["records"]
        for key in ("details", "statistics"):
            self.assertEqual(datetime.fromisoformat(record[key].pop("observed_at")).utcoffset(), timedelta(0))
        self.assertEqual(record, {
            "id": f"unifi:{SITE}:managed_devices:{DEVICE}", "state": "OFFLINE", "firmware_version": "6.6.55",
            "details": {
                "adopted_at": adopted, "provisioned_at": provisioned, "uplink_device_id": OTHER_DEVICE,
                "ports": [{"index": 1, "state": "UP", "connector": "RJ45", "max_speed_mbps": 2500,
                           "speed_mbps": 1000, "poe": {"standard": "802.3at", "enabled": False, "state": "DOWN"}},
                          {"index": 2, "state": "DOWN"}],
                "radios": [{"frequency_ghz": "5", "wlan_standard": "802.11ax", "channel": 36,
                            "channel_width_mhz": 40}],
            },
            "statistics": {
                "uptime_seconds": 0, "last_heartbeat_at": heartbeat,
                "load_average_1_min": 0.125, "load_average_15_min": 1.5,
                "cpu_utilization_pct": 0, "memory_utilization_pct": 12.5,
                "radios": [{"frequency_ghz": "5", "tx_retries_pct": 2.25}],
            },
        })

    def test_unavailable_optional_reads_do_not_change_base_outcomes_or_stop_other_reads(self):
        sections, requests = self.capture(
            response(page({"id": DEVICE, "state": "OFFLINE"}, {"id": OTHER_DEVICE, "state": "ONLINE"})),
            response(page({"id": VPN, "type": "VPN"})),
            response({"error": KEY}, 302, ("Location: https://elsewhere.invalid/",)),
            response({"uptimeSec": False, "cpuUtilizationPct": float("nan"), "memoryUtilizationPct": 101,
                      "lastHeartbeatAt": "2026-10-01", "loadAverage1Min": -1,
                      "interfaces": {"radios": [{"frequencyGHz": "5", "txRetriesPct": "1.5"}]}}),
            response({"id": DEVICE, "adoptedAt": "2026-10-01T12:00:00Z"}),
            response({"uptimeSec": 12}),
            config={**CONFIG, "device_details": True, "device_statistics": True},
        )
        self.assertEqual([request.split(b"\r\n", 1)[0] for request in requests], [
            get("devices"), get("clients"), get_device(DEVICE), get_device(DEVICE, "/statistics/latest"),
            get_device(OTHER_DEVICE), get_device(OTHER_DEVICE, "/statistics/latest"),
        ])
        sections[0]["records"][1]["statistics"].pop("observed_at")
        self.assertEqual(sections, [
            {"section": "managed_devices", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:managed_devices:{DEVICE}", "state": "OFFLINE"},
                {"id": f"unifi:{SITE}:managed_devices:{OTHER_DEVICE}", "state": "ONLINE",
                 "statistics": {"uptime_seconds": 12}},
            ]},
            {"section": "connected_clients", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:connected_clients:{VPN}", "type": "VPN"},
            ]},
        ])

    def test_statistics_only_bounds_requests_and_optional_response_size(self):
        devices = [f"20000000-0000-4000-8000-{n:012x}" for n in range(18)]
        too_large = b'{"uptimeSec": 512}' + b" " * (256 * 1024)
        sections, requests = self.capture(
            response(page(*({"id": device} for device in devices))), response(page()),
            response(too_large), response({"private": KEY}, 404), TimeoutError(), response(b"{"),
            response({"uptimeSec": 7}), *([response({})] * 11),
            config={**CONFIG, "device_statistics": True},
        )
        self.assertEqual([request.split(b"\r\n", 1)[0] for request in requests], [
            get("devices"), get("clients"),
            *(get_device(device, "/statistics/latest") for i, device in enumerate(devices[:16]) if i != 2),
        ])
        records = sections[0]["records"]
        self.assertEqual(sections[0]["outcome"], "complete")
        self.assertEqual([record["id"] for record in records],
                         [f"unifi:{SITE}:managed_devices:{device}" for device in devices])
        self.assertEqual([(i, row["statistics"]["uptime_seconds"])
                          for i, row in enumerate(records) if "statistics" in row], [(4, 7)])
        self.assertFalse(any("details" in row for row in records))

    def test_empty_connected_list_does_not_emit_a_disconnected_record(self):
        before, _ = self.capture(response(page()), response(page({"id": VPN, "type": "VPN"})))
        after, _ = self.capture(response(page()), response(page()))
        self.assertEqual(before[1]["records"], [{"id": f"unifi:{SITE}:connected_clients:{VPN}", "type": "VPN"}])
        self.assertEqual(after[1], {"section": "connected_clients", "outcome": "complete", "records": []})

    def test_auth_failure_and_later_page_failure_do_not_dump_response_or_credentials(self):
        rejection = {"private_response": KEY}
        sections, _ = self.capture(response(rejection, 401),
                                   response(page({"id": VPN, "type": "VPN"}, total=2)),
                                   response(rejection, 503))
        self.assertEqual(sections, [
            {"section": "managed_devices", "outcome": "failed", "records": []},
            {"section": "connected_clients", "outcome": "partial", "records": [
                {"id": f"unifi:{SITE}:connected_clients:{VPN}", "type": "VPN"},
            ]},
        ])

    def test_inconsistent_pages_cannot_establish_completeness(self):
        following = [
            ("wrong offset", page({"id": OTHER_DEVICE}, offset=0, total=2)),
            ("total changed", page({"id": OTHER_DEVICE}, offset=1, total=3)),
            ("no progress", page(offset=1, total=2)),
            ("count mismatch", dict(page({"id": OTHER_DEVICE}, offset=1, total=2), count=0)),
            ("wrong data type", dict(page(offset=1, total=2), data="invalid")),
            ("malformed JSON", b"{not-json"),
        ]
        for label, bad_page in following:
            with self.subTest(label=label):
                sections, _ = self.capture(response(page({"id": DEVICE}, total=2)),
                                           response(bad_page), response(page()))
                self.assertEqual(sections, [
                    {"section": "managed_devices", "outcome": "partial", "records": [
                        {"id": f"unifi:{SITE}:managed_devices:{DEVICE}"},
                    ]},
                    {"section": "connected_clients", "outcome": "complete", "records": []},
                ])

    def test_duplicate_and_invalid_rows_cannot_overwrite_or_establish_absence(self):
        sections, _ = self.capture(
            response(page({"id": DEVICE, "name": "Original"}, total=4)),
            response(page({"id": DEVICE.upper(), "name": "Conflicting"}, {"id": "missing-uuid"},
                          {"id": OTHER_DEVICE, "state": "OFFLINE"}, offset=1, total=4)),
            response(page({"id": VPN, "name": {"private": KEY}})),
        )
        self.assertEqual(sections, [
            {"section": "managed_devices", "outcome": "partial", "records": [
                {"id": f"unifi:{SITE}:managed_devices:{DEVICE}", "name": "Original"},
                {"id": f"unifi:{SITE}:managed_devices:{OTHER_DEVICE}", "state": "OFFLINE"},
            ]},
            {"section": "connected_clients", "outcome": "failed", "records": []},
        ])

    def test_never_follows_redirects(self):
        sections, requests = self.capture(
            response({}, 302, ("Location: https://elsewhere.invalid/",)),
            response({}, 307, (f"Location: {ORIGIN}/other-site",)),
        )
        self.assertEqual([request.split(b"\r\n", 1)[0] for request in requests], [get("devices"), get("clients")])
        self.assertEqual([section["outcome"] for section in sections], ["failed", "failed"])

    def test_ipv6_origin_without_port_uses_https_default(self):
        sections, requests = self.capture(
            response(page()), response(page()),
            config={**CONFIG, "base_url": "https://[2001:db8::1]"}, origin=("2001:db8::1", 443),
        )
        for request in requests:
            self.assertIn(b"\r\nHost: [2001:db8::1]\r\n", request)
        self.assertEqual([section["outcome"] for section in sections], ["complete", "complete"])

    def test_tls_failure_sends_no_requests(self):
        failure = ssl.SSLCertVerificationError("certificate rejected")
        sections, requests = self.capture(failure, failure)
        self.assertEqual(requests, [])
        self.assertEqual([section["outcome"] for section in sections], ["failed", "failed"])

    def test_bad_configuration_and_missing_opt_in_do_not_contact_console(self):
        changes = [
            {"enabled": False}, {"enabled": 1}, {"api_key_env": "UNSET_SYNTHETIC_KEY", "api_key": KEY},
            {"base_url": "http://console.example.test:8443"},
            {"base_url": "https://someuser:somekey@console.example.test:8443"},
            {"base_url": "https://console.example.test:8443/?token=synthetic"},
            {"base_url": "https://api.ui.com"}, {"site_id": "../sites"},
        ]
        for change in changes:
            with self.subTest(change=change):
                sections, _ = self.capture(config={**CONFIG, **change})
                self.assertEqual(sections, [
                    {"section": "managed_devices", "outcome": "failed", "records": []},
                    {"section": "connected_clients", "outcome": "failed", "records": []},
                ])
