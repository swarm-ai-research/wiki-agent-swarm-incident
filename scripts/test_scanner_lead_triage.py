import json
import unittest
from pathlib import Path

import scanner_lead_triage as triage


ROOT = Path(__file__).resolve().parents[1]


class ScannerLeadTriageTests(unittest.TestCase):
    def test_selects_only_readable_rows_in_score_order(self):
        payload = {"results": [
            {"name": "low", "engine": "usemod", "url": "https://low", "outcome": "readable", "score": 2},
            {"name": "blocked", "engine": "usemod", "url": "https://blocked", "outcome": "blocked", "score": None},
            {"name": "high", "engine": "usemod", "url": "https://high", "outcome": "readable", "score": 8},
        ]}
        selected = triage.select(payload, 2, known_names=("high",))
        self.assertEqual([r["name"] for r in selected], ["high", "low"])
        self.assertTrue(selected[0]["prior_known_incident_host"])

    def test_saved_manifest_pins_top_fifteen(self):
        rows = json.loads((ROOT / "data/swarm_scan_top15_manifest_2026-09-08.json").read_text())
        self.assertEqual(len(rows), 15)
        self.assertEqual(rows[0]["name"], "DorfWiki")
        self.assertEqual(rows[-1]["name"], "Czech Wikiversity")

    def test_all_leads_receive_bounded_dispositions(self):
        audit = json.loads((ROOT / "data/swarm_scan_top15_triage_2026-09-08.json").read_text())
        self.assertEqual(len(audit["leads"]), 15)
        self.assertEqual(sum(audit["summary"][k] for k in (
            "known_incident_related", "false_positive", "unresolved_detector_only"
        )), 15)
        self.assertEqual(audit["summary"]["new_confirmed_host"], 0)
        self.assertTrue(all(row["basis"] for row in audit["leads"]))
        self.assertEqual(
            {row["disposition"] for row in audit["leads"]},
            {"known_incident_related", "false_positive", "unresolved_detector_only"},
        )


if __name__ == "__main__":
    unittest.main()
