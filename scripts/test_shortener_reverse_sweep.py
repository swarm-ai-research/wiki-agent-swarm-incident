import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import shortener_reverse_sweep as sweep


class ShortenerReverseSweepTests(unittest.TestCase):
    def test_refuses_bare_short_code_but_allows_listings_and_stats(self):
        hosts = {"sho.rt"}
        self.assertFalse(sweep.safe_listing_url("https://sho.rt/abc123", hosts))
        self.assertTrue(sweep.safe_listing_url("https://sho.rt/admin/index.php?perpage=100", hosts))
        self.assertTrue(sweep.safe_listing_url("https://sho.rt/abc123+", hosts))

    def test_query_code_reads_cdx_redirect_without_requesting_short_url(self):
        body = json.dumps([
            ["timestamp", "original", "statuscode", "mimetype", "digest", "redirect"],
            ["20260501000000", "https://sho.rt/a", "302", "text/html", "D", "https://example.org/data"],
        ])
        with mock.patch.object(sweep, "fetch", return_value=(200, body, None, None)) as fetch:
            result = sweep.query_code("sho.rt/a", 5)
        self.assertIn("web.archive.org/cdx/", fetch.call_args.args[0])
        self.assertEqual(result["redirect_targets"], ["https://example.org/data"])

    def test_redacts_sensitive_redirect_query_values(self):
        value = sweep.redact_url("https://example.org/data?api_key=secret&year=2026")
        self.assertNotIn("secret", value)
        self.assertIn("year=2026", value)

    def test_listing_pairs_only_within_same_row(self):
        body = """
        <table><tr><td>sho.rt/a</td><td>https://example.org/data.csv</td></tr>
        <tr><td>sho.rt/b</td><td>https://example.org/other.csv</td></tr></table>
        """
        result = sweep.scan_listing(
            body, ["sho.rt/a", "sho.rt/b"],
            {"end:data": ["example.org/data.csv"], "end:other": ["example.org/other.csv"]},
        )
        self.assertEqual(result["row_pairs"], [
            {"short_code": "sho.rt/a", "target_id": "end:data"},
            {"short_code": "sho.rt/b", "target_id": "end:other"},
        ])

    def test_parallel_preserves_input_order(self):
        self.assertEqual(sweep._parallel([3, 1, 2], lambda value: value * 2, 2, 0), [6, 2, 4])

    def test_listing_url_hostname_is_trusted_when_registry_host_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE venue (id text, host text, kind text, listing_urls text)")
            connection.execute(
                "INSERT INTO venue VALUES (?, ?, ?, ?)",
                ("bitily", "app.bitily.in", "shortener", json.dumps(["https://bitily.in/X/admin/index.php"])),
            )
            connection.commit()
            connection.close()
            hosts, listings = sweep.load_listings(path)
        self.assertIn("bitily.in", hosts)
        self.assertTrue(sweep.safe_listing_url(listings[0]["url"], hosts))


if __name__ == "__main__":
    unittest.main()
