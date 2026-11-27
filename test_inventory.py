"""inventory.absence-requires-complete-scan

Only a successful, complete scan of the declared scope can establish absence.
A failed or partial scan cannot replace a prior observation: retain the last
verified state and show its age, or report unknown if there is no evidence.
"""

import json
from pathlib import Path
import unittest

from inventory import compare


EXAMPLES = Path(__file__).with_name("examples")


def example(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


class AbsenceRequiresCompleteScan(unittest.TestCase):
    """Contract: inventory.absence-requires-complete-scan."""

    def setUp(self):
        self.declarations = example("declarations")
        self.baseline = example("baseline")

    def results(self, *scans):
        return {row["name"]: row for row in compare(self.declarations, list(scans))}

    def test_complete_omission_establishes_absence(self):
        rows = self.results(self.baseline, example("complete"))
        self.assertEqual(rows["paper-moon"]["status"], "absent")
        self.assertIsNone(rows["paper-moon"]["observed"])

    def test_complete_scan_of_other_project_cannot_establish_absence(self):
        with self.assertRaisesRegex(ValueError, "same server/project"):
            self.results(dict(example("complete"), scope="other-theatre"))

    def test_declarations_without_collection_are_not_connected(self):
        row = self.results()["paper-moon"]
        self.assertEqual(row["status"], "not connected")
        self.assertIsNone(row["observation_source"])

    def test_complete_scan_compares_effective_fields_and_keeps_state(self):
        rows = self.results(self.baseline, example("complete"))
        stopped = rows["velvet-curtain"]
        self.assertEqual(stopped["status"], "match")
        self.assertEqual(stopped["observed"]["state"], "Stopped")
        self.assertEqual(stopped["differences"], [])
        mismatch = rows["tin-piano"]
        self.assertEqual(mismatch["status"], "disagreement")
        self.assertEqual(mismatch["differences"], ["cpu"])
        self.assertEqual(mismatch["observed"]["kind"], "virtual-machine")
        self.assertIsNotNone(mismatch["observed"]["memory"])
        undeclared = rows["painted-sun"]
        self.assertEqual(undeclared["status"], "observed and undeclared")
        self.assertIsNone(undeclared["declared"])

    def assert_retained(self, scan, age_seconds):
        prior = self.results(self.baseline)["paper-moon"]
        row = self.results(self.baseline, scan)["paper-moon"]
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["observed"], prior["observed"])
        self.assertEqual(row["observed"]["state"], "Running")
        self.assertEqual(row["observation_source"], {
            "source": "synthetic-incus", "scope": "toy-theatre",
            "timestamp": "2025-01-01T10:00:00Z",
        })
        self.assertEqual(row["age_seconds"], age_seconds)
        self.assertEqual(row["scan"]["timestamp"], scan["timestamp"])

    def test_failed_omission_retains_last_observation(self):
        self.assert_retained(example("failed"), 600)

    def test_partial_scan_cannot_replace_last_observation(self):
        partial = dict(example("failed"), outcome="partial", timestamp="2025-01-01T10:03:00Z")
        self.assert_retained(partial, 180)
        partial["instances"] = [dict(self.baseline["instances"][0], status="Stopped")]
        self.assert_retained(partial, 180)

    def test_incomplete_scan_without_prior_evidence_reports_unknown(self):
        for outcome in ("failed", "partial"):
            with self.subTest(outcome=outcome):
                row = self.results(dict(example("failed"), outcome=outcome))["paper-moon"]
                self.assertEqual(row["status"], "unknown")
                self.assertIsNone(row["observed"])
                self.assertIsNone(row.get("age_seconds"))


if __name__ == "__main__":
    unittest.main()
