import unittest
import json
from pathlib import Path

import kmad_sweep_reproduction as reproduction


class KmadSweepReproductionTests(unittest.TestCase):
    def test_retry_manifest_selects_only_non_readable_outcomes(self):
        payload = {"results": [
            {"name": "ok", "engine": "usemod", "url": "https://ok", "outcome": "readable"},
            {"name": "blocked", "engine": "usemod", "url": "https://blocked", "outcome": "blocked"},
            {"name": "bad", "engine": "usemod", "url": "https://bad", "outcome": "parsing_failed"},
        ]}
        rows = reproduction.retry_manifest(payload)
        self.assertEqual({row["name"] for row in rows}, {"blocked", "bad"})
        self.assertTrue(all(row["status"] == "Active" for row in rows))

    def test_saved_reproduction_reconciles_retry_and_controls(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root / "data/kmad_sweep_reproduction_2026-09-08.json").read_text())
        coverage = report["coverage"]
        self.assertEqual(coverage["retry_eligible"], 1117)
        self.assertEqual(sum(coverage["retry_transitions"].values()), 1117)
        self.assertEqual(sum(coverage["final"].values()), 1355)
        self.assertTrue(all(control["passed"] for control in report["positive_controls"]))
        self.assertEqual(report["scope"]["equivalence_to_kmad_population"], "not established")


if __name__ == "__main__":
    unittest.main()
