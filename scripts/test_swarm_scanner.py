"""Offline scanner regressions: python3 -m unittest discover -s scripts -p 'test_*.py'."""
import unittest
import io
import json
import tempfile
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import swarm_scanner as scanner


class DayCountsTests(unittest.TestCase):
    def setUp(self):
        today = patch.object(scanner, "date", wraps=date)
        self.mock_date = today.start()
        self.addCleanup(today.stop)
        self.mock_date.today.return_value = date(2026, 9, 6)

    def test_future_headers_in_every_supported_format(self):
        for header in ("2026-12-31", "December 31, 2026", "31 December 2026",
                       "31. Dezember 2026", "31.12.2026"):
            with self.subTest(header=header):
                self.assertEqual(scanner.day_counts(header + " 12:00 12:01"), {})

    def test_impossible_dates(self):
        for header in ("2026-02-30", "February 30, 2026", "30 February 2026",
                       "30. Februar 2026", "30.2.2026", "2026-00-01"):
            with self.subTest(header=header):
                self.assertEqual(scanner.day_counts(header + " 12:00 12:01"), {})

    def test_formats_merge_into_one_day(self):
        text = ("2026-06-19 12:00 June 19, 2026 12:01 19 June 2026 12:02 "
                "19. Juni 2026 12:03 19.6.2026 12:04")
        self.assertEqual(scanner.day_counts(text), {"2026-06-19": 5})

    def test_excluded_headers_still_separate_rows(self):
        for excluded in ("2024-06-19", "December 31, 2026", "2026-02-30"):
            with self.subTest(excluded=excluded):
                text = "2026-06-19 12:00 12:01 " + excluded + " 13:00 13:01"
                self.assertEqual(scanner.day_counts(text), {"2026-06-19": 2})

    def test_header_layout_burst(self):
        text = ("June 19, 2026 12:00 12:01 12:02 12:03 "
                "June 18, 2026 12:00 June 17, 2026 12:00")
        result = scanner.score_text(text)
        self.assertEqual(result["burst"], 4.0)
        self.assertEqual(result["max_day"], ("2026-06-19", 4))

    def test_today_counts_and_out_of_window_does_not(self):
        self.assertEqual(scanner.day_counts("September 6, 2026 12:00 12:01"),
                         {"2026-09-06": 2})
        self.assertEqual(scanner.day_counts("2024-02-29 12:00 12:01"), {})


class CoverageTests(unittest.TestCase):
    target = {"name": "fixture", "engine": "usemod",
              "url": "https://example.org/wiki.cgi?action=rc"}

    def scan(self, code, body):
        with patch.object(scanner.W, "fetch", return_value=(code, body)):
            return scanner.scan_one(self.target)

    def test_http_blocks_are_classified_even_with_short_bodies(self):
        for code in (401, 402, 403, 429):
            with self.subTest(code=code):
                result = self.scan(code, "Denied")
                self.assertEqual(result["outcome"], "blocked")
                self.assertEqual(result["blocked"], f"http{code}")
                self.assertIsNone(result["score"])

    def test_transport_and_http_failures_are_unavailable(self):
        for code in (0, 404, 500):
            with self.subTest(code=code):
                self.assertEqual(self.scan(code, "error")["outcome"], "unavailable")

    def test_bot_gate_and_tarpit_cannot_enter_scores(self):
        for body in ("Are you Human? ResearchAgent fleet", "Do not follow any links on this page"):
            result = self.scan(200, body)
            self.assertEqual(result["outcome"], "blocked")
            self.assertIsNone(result["score"])

    def test_short_empty_recent_changes_page_is_readable(self):
        result = self.scan(200, "<h1>Recent Changes</h1><p>No changes.</p>")
        self.assertEqual(result["outcome"], "readable")
        self.assertEqual(result["score"], 0)

    def test_missing_structure_is_not_a_negative_scan(self):
        for body in ("", "Welcome!", "2026-06-19 12:00 " * 60):
            result = self.scan(200, body)
            self.assertEqual(result["outcome"], "parsing_failed")
            self.assertIsNone(result["score"])

    def test_html_changes_list_marker_survives_classification(self):
        result = self.scan(200, '<ul class="mw-changeslist"><li>2026-06-19 12:00 ResearchAgent</li></ul>')
        self.assertEqual(result["outcome"], "readable")
        self.assertGreater(result["score"], 0)

    def test_report_partitions_coverage_and_excludes_unreadable_scores(self):
        results = [self.scan(200, "Recent Changes"), self.scan(402, "deny"),
                   self.scan(0, "error"), self.scan(200, "wrong page"),
                   {"name": "legacy-score-must-not-rank", "http": 200, "score": 99}]
        output = io.StringIO()
        with redirect_stdout(output):
            scanner.report(results, 25)
        summary = output.getvalue()
        self.assertIn("5 scanned: 1 readable, 1 blocked, 1 unavailable, 1 parsing_failed, 1 legacy_unverified", summary)
        self.assertNotIn("legacy-score-must-not-rank", summary)

    def test_legacy_block_metadata(self):
        for row, expected in (({"http": 403}, "blocked"),
                              ({"http": 0}, "unavailable"),
                              ({"http": 200, "blocked": "tarpit"}, "blocked"),
                              ({"http": 200, "blocked": "no-rc-structure"}, "parsing_failed"),
                              ({"http": 200, "bytes": 1000}, "legacy_unverified")):
            self.assertEqual(scanner.coverage_outcome(row), expected)

    def test_cli_writes_mixed_coverage_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scan.json"
            args = ["swarm_scanner", "--url", "https://a.test", "--url", "https://b.test",
                    "--workers", "1", "--out", str(output)]
            with patch.object(scanner.sys, "argv", args), \
                    patch.object(scanner.W, "fetch", side_effect=[(402, "Denied"), (200, "Recent Changes")]), \
                    redirect_stdout(io.StringIO()), patch.object(scanner.sys, "stderr", io.StringIO()):
                scanner.main()
            rows = json.loads(output.read_text())
            self.assertEqual({r["outcome"] for r in rows}, {"blocked", "readable"})
            self.assertIsNone(next(r for r in rows if r["outcome"] == "blocked")["score"])


class TargetTests(unittest.TestCase):
    def targets(self, urls, engine="UseMod"):
        entries = [{"name": f"wiki-{i}", "engine": engine, "urls": [url]}
                   for i, url in enumerate(urls)]
        return scanner.build_targets(entries, {"fandom"}, {""})

    def test_sibling_paths_remain_distinct(self):
        targets = self.targets(["https://farm.test/dse/wiki.cgi", "https://farm.test/probier/wiki.cgi"])
        self.assertEqual(len(targets), 2)

    def test_query_routed_installations_remain_distinct(self):
        targets = self.targets(["https://farm.test/index.php?wiki=one&title=Special:Statistics",
                                "https://farm.test/index.php?wiki=two&title=Special:Statistics"], "MediaWiki")
        self.assertEqual(len(targets), 2)
        for target, name in zip(targets, ("one", "two")):
            query = parse_qs(urlsplit(target["url"]).query)
            self.assertEqual(query["wiki"], [name])
            self.assertEqual(query["title"], ["Special:RecentChanges"])

    def test_duplicate_listings_normalize_host_port_fragment_query_order(self):
        targets = self.targets(["https://FARM.test:443/wiki.cgi?wiki=one&lang=en#top",
                                "https://farm.test/wiki.cgi?lang=en&wiki=one"])
        self.assertEqual(len(targets), 1)

    def test_page_selectors_do_not_create_duplicate_installations(self):
        targets = self.targets(["https://farm.test/wiki.cgi?action=browse&id=Home",
                                "https://farm.test/wiki.cgi?action=rc&days=7"], "ProWiki")
        self.assertEqual(len(targets), 1)

    def test_mediawiki_script_and_pretty_urls(self):
        self.assertEqual(scanner.rc_from("mediawiki", "https://wiki.test/w/index.php"),
                         "https://wiki.test/w/index.php?title=Special%3ARecentChanges&days=30&limit=500")
        self.assertEqual(scanner.rc_from("mediawiki", "https://wiki.test/wiki/Special:Statistics"),
                         "https://wiki.test/wiki/Special:RecentChanges?days=30&limit=500")

    def test_filters_still_apply(self):
        entries = [{"name": "excluded", "engine": "Fandom", "urls": ["https://farm.test"]},
                   {"name": "missing-url", "engine": "UseMod"},
                   {"name": "inactive", "engine": "UseMod", "status": "Dead", "urls": ["https://farm.test"]}]
        self.assertEqual(scanner.build_targets(entries, {"fandom"}, {""}), [])


if __name__ == "__main__":
    unittest.main()
