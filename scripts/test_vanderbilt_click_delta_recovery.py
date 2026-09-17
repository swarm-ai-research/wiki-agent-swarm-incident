import csv
import unittest
from pathlib import Path

import vanderbilt_click_delta_recovery as recovery


class VanderbiltClickDeltaRecoveryTests(unittest.TestCase):
    def test_parse_all_time(self):
        html = "<a href='#stat_line_all'>All time</a></span> <span class='historical_count'>17,294 hits</span>"
        self.assertEqual(recovery.parse_all_time(html), 17294)
        self.assertIsNone(recovery.parse_all_time("no chart"))

    def test_alias_from_stats_url(self):
        self.assertEqual(recovery.alias_from_url("https://vanderbi.lt/OpenAIPovertyCompactTest+"), "OpenAIPovertyCompactTest")

    def test_api_rows_are_case_insensitive_and_keep_largest_counter(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "links.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["alias", "clicks"])
                writer.writeheader()
                writer.writerows([
                    {"alias": "MixedCase", "clicks": "3"},
                    {"alias": "mixedcase", "clicks": "5"},
                ])
            rows = recovery.load_api_rows(path)
        self.assertEqual(rows["mixedcase"]["clicks"], "5")

    def test_committed_recovery_has_a_disposition_for_every_kmad_capture(self):
        import json

        report = json.loads((recovery.ROOT / "data" / "vanderbilt_click_delta_recovery_2026-09-08.json").read_text())
        self.assertEqual(len(report["kmad_population_comparison"]), report["summary"]["kmad_captures"])
        self.assertEqual(report["summary"]["comparable_links"], 25)
        self.assertEqual(report["summary"]["aggregate_delta"], 266)


if __name__ == "__main__":
    unittest.main()
