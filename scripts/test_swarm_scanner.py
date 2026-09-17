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
        self.assertEqual(result["max_day"], ["2026-06-19", 4])

    def test_today_counts_and_out_of_window_does_not(self):
        self.assertEqual(scanner.day_counts("September 6, 2026 12:00 12:01"),
                         {"2026-09-06": 2})
        self.assertEqual(scanner.day_counts("2024-02-29 12:00 12:01"), {})


class EditRowTests(unittest.TestCase):
    def setUp(self):
        today = patch.object(scanner, "date", wraps=date)
        self.mock_date = today.start()
        self.addCleanup(today.stop)
        self.mock_date.today.return_value = date(2026, 9, 6)

    def test_mediawiki_counts_revision_rows_not_other_timestamps(self):
        body = '''<div class="mw-changeslist"><h4>6 September 2026</h4>
          <time>12:01</time><li class="mw-changeslist-line" data-mw-revid="10" data-mw-ts="20260906120000">edit</li>
          <tr class="mw-changeslist-line" data-mw-logid="11" data-mw-ts="20260906120100"><td>log</td></tr>
          <footer>Last edited 2026-09-06 23:59</footer></div>'''
        counts, coverage = scanner.edit_row_counts(body, "mediawiki")
        self.assertEqual(counts, {"2026-09-06": 2})
        self.assertEqual(coverage["row_parse_outcome"], "parsed")
        self.assertEqual(coverage["edit_rows_seen"], 2)

    def test_usemod_counts_list_items_once_despite_summary_timestamps(self):
        body = '''<div class="wikirc"><p><strong>September 6, 2026</strong></p><ul>
          <li>Page 12:00 summary mentions 09:11 and 2026-09-01</li>
          <li>Page 12:01</li></ul><p><strong>September 5, 2026</strong></p>
          <ul><li>Page 08:00</li></ul></div><footer>Last edited September 6, 2026 23:59</footer>'''
        counts, coverage = scanner.edit_row_counts(body, "usemod")
        self.assertEqual(counts, {"2026-09-06": 2, "2026-09-05": 1})
        self.assertEqual(coverage["edit_rows_counted"], 3)

    def test_oddmuse_uses_rc_container_and_ignores_navigation_lists(self):
        body = '''<ul><li>navigation 12:00</li></ul><div class="rc">
          <p><strong>September 6, 2026</strong></p><ul><li><span class="time">10:00</span> Page</li></ul>
          </div>'''
        counts, coverage = scanner.edit_row_counts(body, "oddmuse")
        self.assertEqual(counts, {"2026-09-06": 1})
        self.assertEqual(coverage["row_parser"], "oddmuse")

    def test_empty_listing_and_unknown_layout_are_explicit(self):
        counts, coverage = scanner.edit_row_counts('<div class="wikirc"><p>No changes.</p></div>', "usemod")
        self.assertEqual(counts, {})
        self.assertEqual(coverage["row_parse_outcome"], "empty")
        counts, coverage = scanner.edit_row_counts("Recent Changes 2026-09-06 12:00", "usemod")
        self.assertEqual(counts, {})
        self.assertEqual(coverage["row_parse_outcome"], "unrecognized_layout")

    def test_unsupported_engine_does_not_guess_from_arbitrary_timestamps(self):
        counts, coverage = scanner.edit_row_counts("2026-09-06 12:00 12:01", "dokuwiki")
        self.assertEqual(counts, {})
        self.assertEqual(coverage["row_parse_outcome"], "unsupported_engine")


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

    def test_scan_uses_structural_rows_for_burst_and_records_coverage(self):
        body = '''<h1>Recent Changes</h1><div class="wikirc">
          <p><strong>September 6, 2026</strong></p><ul>
          <li>Page 12:00 summary repeats 11:11 10:10</li><li>Page 12:01</li></ul>
          <p><strong>September 5, 2026</strong></p><ul><li>Page 09:00</li></ul></div>'''
        result = self.scan(200, body)
        self.assertEqual(result["max_day"], ["2026-09-06", 2])
        self.assertEqual(result["edit_rows_seen"], 3)
        self.assertEqual(result["row_parse_outcome"], "parsed")

    def test_result_persists_hash_timestamps_windows_and_rescore_inputs(self):
        body = '''<h1>Recent Changes</h1><div class="wikirc">
          <p><strong>September 6, 2026</strong></p><ul><li>ResearchAgent 12:00</li></ul>
          </div>'''
        target = dict(self.target, url=self.target["url"] + "&days=200&all=1")
        result = scanner.scan_response(target, 200, body, fetched_at="2026-09-08T12:00:00Z")
        self.assertEqual(result["fetched_at"], "2026-09-08T12:00:00Z")
        self.assertEqual(result["content_sha256"], scanner.sha256_text(body))
        self.assertEqual(result["requested_window"]["parameters"]["days"], "200")
        self.assertEqual(result["observed_window"]["from"], "2026-09-06")
        self.assertEqual(result["day_counts"], {"2026-09-06": 1})
        rescored = scanner.rescore_result(result)
        for key in ("score", "burst", "days_2526", "max_day"):
            self.assertEqual(rescored[key], result[key])

    def test_page_excerpts_are_opt_in(self):
        body = '<h1>Recent Changes</h1><div class="wikirc"><p><strong>September 6, 2026</strong></p><ul><li>ResearchAgent 12:00</li></ul></div>'
        default = scanner.scan_response(self.target, 200, body, "2026-09-08T12:00:00Z")
        included = scanner.scan_response(
            self.target, 200, body, "2026-09-08T12:00:00Z", include_evidence=True
        )
        self.assertEqual(default["evidence"], [])
        self.assertTrue(included["evidence"])

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
            payload = json.loads(output.read_text())
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["run"]["state"], "complete")
            self.assertEqual(payload["run"]["target_count"], 2)
            self.assertEqual(len(payload["run"]["scanner"]["sha256"]), 64)
            rows = payload["results"]
            self.assertEqual({r["outcome"] for r in rows}, {"blocked", "readable"})
            self.assertIsNone(next(r for r in rows if r["outcome"] == "blocked")["score"])

    def test_v2_report_envelope_is_accepted(self):
        payload = {"schema_version": 2, "run": {"state": "complete", "started_at": "now",
                   "scanner": {"sha256": "a" * 64}},
                   "results": [self.scan(200, "Recent Changes")]}
        output = io.StringIO()
        with redirect_stdout(output):
            scanner.report(payload, 25)
        self.assertIn("run schema v2", output.getvalue())
        self.assertIn("offline rescore: 1/1 stored scores match", output.getvalue())


class CalibrationTests(unittest.TestCase):
    def test_authored_positive_negative_and_gate_fixtures_pass(self):
        calibration = scanner.run_calibration()
        self.assertTrue(calibration["authored_fixtures"])
        rows = {row["fixture_id"]: row for row in calibration["results"]}
        self.assertEqual(set(rows), {"synthetic-swarm", "busy-human", "bot-gate"})
        self.assertTrue(all(row["calibration_passed"] for row in rows.values()))
        self.assertGreater(rows["synthetic-swarm"]["score"], rows["busy-human"]["score"])
        self.assertEqual(rows["bot-gate"]["outcome"], "blocked")
        self.assertTrue(all(not row["evidence"] for row in rows.values()))


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
