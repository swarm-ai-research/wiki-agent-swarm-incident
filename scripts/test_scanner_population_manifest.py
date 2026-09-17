import json
import unittest
from pathlib import Path

import scanner_population_manifest as manifest
import swarm_scanner as scanner


ROOT = Path(__file__).resolve().parents[1]


class ScannerPopulationManifestTests(unittest.TestCase):
    def test_derivation_is_deterministic_and_deduplicates_urls(self):
        rows = [
            {"name": "A", "engine": "usemod", "url": "https://a.test/wiki.pl?action=rc"},
            {"name": "A duplicate", "engine": "usemod", "url": "https://a.test/wiki.pl?action=rc"},
        ]
        self.assertEqual(manifest.derive(rows), manifest.derive(list(rows)))
        self.assertEqual(len(manifest.derive(rows)), 1)

    def test_saved_manifest_reconstructs_the_declared_population(self):
        entries = json.loads((ROOT / "data/wikiindex_population_2026-09-06.json").read_text())
        targets = scanner.build_targets(entries, {"wikia", "fandom"}, {"Active"})
        self.assertEqual(len(entries), 1356)
        # The current installation-key normalizer merges one equivalent RC URL.
        self.assertEqual(len(targets), 1355)
        self.assertTrue(all(set(e) == {"name", "engine", "status", "urls"} for e in entries))


if __name__ == "__main__":
    unittest.main()
