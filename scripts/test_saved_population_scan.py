import collections
import hashlib
import json
import unittest
from pathlib import Path

import swarm_scanner as scanner


ROOT = Path(__file__).resolve().parents[1]


class SavedPopulationScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "data/swarm_scan_2026-09-08.json"
        cls.payload = json.loads(cls.path.read_text())

    def test_population_and_outcomes_reconcile(self):
        results = self.payload["results"]
        counts = collections.Counter(row["outcome"] for row in results)
        self.assertEqual(self.payload["schema_version"], 2)
        self.assertEqual(self.payload["run"]["state"], "complete")
        self.assertEqual(self.payload["run"]["target_count"], 1355)
        self.assertEqual(len(results), 1355)
        self.assertEqual(
            counts,
            {"readable": 238, "blocked": 540, "unavailable": 488, "parsing_failed": 89},
        )

    def test_input_and_target_hashes_are_pinned(self):
        manifest = ROOT / "data/wikiindex_population_2026-09-06.json"
        self.assertEqual(
            hashlib.sha256(manifest.read_bytes()).hexdigest(),
            self.payload["run"]["source"]["sha256"],
        )
        self.assertEqual(
            self.payload["run"]["targets_sha256"],
            "b2a5186618c480efa66b390dc36de22220c0e8d19e7a748ffc29a53eb6cfa11f",
        )

    def test_every_readable_score_reproduces_offline(self):
        config = self.payload["run"]["configuration"]["scoring"]
        readable = [r for r in self.payload["results"] if r["outcome"] == "readable"]
        self.assertEqual(len(readable), 238)
        self.assertTrue(all(scanner.rescore_result(r, config)["score"] == r["score"] for r in readable))
        self.assertFalse(any(scanner.coverage_outcome(r) == "legacy_unverified" for r in self.payload["results"]))


if __name__ == "__main__":
    unittest.main()
