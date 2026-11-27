"""UniFi collector tests over synthetic HTTP responses; no socket is opened."""

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
                           "ipAddress": "192.0.2.1", "firmwareUpdatable": True}, total=2,
                          next="https://elsewhere.invalid/")),
            response(page({"id": OTHER_DEVICE, "name": "Lobby AP", "state": "ONLINE"}, offset=1, total=2)),
            response(page({"id": DEVICE, "name": "Ticket booth", "type": "WIRELESS",
                           "macAddress": "02:00:00:00:00:02", "access": {"ignored": True}}, total=2)),
            response(page({"id": VPN, "name": "Remote crew", "type": "VPN", "macAddress": None},
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
                 "state": "OFFLINE", "mac_address": "02:00:00:00:00:01", "ip_address": "192.0.2.1"},
                {"id": f"unifi:{SITE}:managed_devices:{OTHER_DEVICE}", "name": "Lobby AP", "state": "ONLINE"},
            ]},
            {"section": "connected_clients", "outcome": "complete", "records": [
                {"id": f"unifi:{SITE}:connected_clients:{DEVICE}", "name": "Ticket booth", "type": "WIRELESS",
                 "mac_address": "02:00:00:00:00:02"},
                {"id": f"unifi:{SITE}:connected_clients:{VPN}", "name": "Remote crew", "type": "VPN"},
            ]},
        ])

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
