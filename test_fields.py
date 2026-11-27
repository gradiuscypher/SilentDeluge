"""Check comparison semantics through the inventory report, not the limit parser."""

import unittest

from inventory import compare


def report(declared, effective, *, local=None, kind="container"):
    declarations = {
        "source": "synthetic definitions", "scope": "example", "timestamp": "2025-01-01T00:00:00Z",
        "containers": [{"name": "guest", **declared}], "vms": [],
    }
    scan = {
        "source": "synthetic scan", "scope": "example", "timestamp": "2025-01-01T00:01:00Z",
        "outcome": "complete", "instances": [{
            "name": "guest", "type": kind, "status": "Stopped",
            "config": local or {}, "expanded_config": effective,
        }],
    }
    row, = compare(declarations, [scan])
    return row


class FieldComparisonTests(unittest.TestCase):
    def test_only_effective_limits_are_evidence(self):
        local = {"limits.cpu": "6", "limits.memory": "2GiB"}
        row = report({"cpu": 6}, {"limits.cpu": "2"}, local=local)
        self.assertEqual(row["status"], "disagreement")
        self.assertEqual(row["differences"], ["cpu"])
        self.assertEqual(row["observed"], {
            "kind": "container", "state": "Stopped", "cpu": "2", "memory": None,
        })

    def test_equivalent_limits_preserve_reported_spelling(self):
        for declared_cpu, effective_cpu in [(4, "4"), ("3,1-2,0", "0-3")]:
            with self.subTest(cpu=declared_cpu):
                row = report({"cpu": declared_cpu, "memory": "1GiB"}, {
                    "limits.cpu": effective_cpu, "limits.memory": "1024MiB",
                })
                self.assertEqual(row["status"], "match")
                self.assertEqual(row["differences"], [])
                self.assertEqual(row["observed"]["memory"], "1024MiB")

    def test_kind_count_pinning_and_decimal_binary_units_are_distinct(self):
        row = report({"cpu": 4, "memory": "1GB"}, {
            "limits.cpu": "0-3", "limits.memory": "1GiB",
        }, kind="virtual-machine")
        self.assertEqual(row["status"], "disagreement")
        self.assertEqual(row["differences"], ["kind", "cpu", "memory"])

    def test_pin_sets_compare_membership_not_just_cardinality(self):
        row = report({"cpu": "0-3"}, {"limits.cpu": "4-7"})
        self.assertEqual(row["differences"], ["cpu"])

    def test_undeclared_limits_remain_visible_without_disagreement(self):
        row = report({}, {"limits.cpu": "2", "limits.memory": "3GB"})
        self.assertEqual(row["status"], "match")
        self.assertEqual(row["observed"]["cpu"], "2")
        self.assertEqual(row["observed"]["memory"], "3GB")
