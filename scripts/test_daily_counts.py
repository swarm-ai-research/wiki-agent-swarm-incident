"""Offline checks: held daily_counts.json matches the timeline.html fallback."""
import json
import unittest
from pathlib import Path

import daily_counts as dc


class HeldTimelineSync(unittest.TestCase):
    def test_embedded_rows_match_held_json(self):
        data = json.loads((dc.ROOT / "data" / "daily_counts.json").read_text())
        html = (dc.ROOT / "timeline.html").read_text()
        embedded = dc.extract_embedded_rows(html)
        self.assertEqual(embedded, data["rows"])

    def test_held_totals_are_the_published_cut(self):
        # Guard against accidental re-aggregation inventing a new export cut.
        data = json.loads((dc.ROOT / "data" / "daily_counts.json").read_text())
        saves = sum(
            r["dse"] + r["probier"] + r["fractal"] + r["wiki4d"] + r["other"]
            for r in data["rows"]
        )
        dels = sum(r["del"] for r in data["rows"])
        self.assertEqual(saves, 15007)
        self.assertEqual(dels, 5217)
        self.assertEqual(sum(r["dse"] for r in data["rows"]), 13403)


if __name__ == "__main__":
    unittest.main()
