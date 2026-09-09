#!/usr/bin/env python3
"""Fixture tests for shortener_export_crosscheck.py. No network."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortener_export_crosscheck as sec  # noqa: E402


def rev(**kw):
    base = {"body": "", "name": "", "page_id": "", "change_summary": ""}
    base.update(kw)
    return base


REVISIONS = [
    rev(body="see https://vanderbi.lt/massjson715 for the cache",
        name="AgentMassJson"),
    rev(body="https://vanderbi.lt/yourls-go.php?id=bwkug wraps "
             "https://www.sec.gov/files/county.json"),
    rev(body="Describe the new page here.", name="OAIIPEDSMay16Map0"),
    rev(body="fetch http://api.datausa.io/api/data?measure=x via "
             "https://md.succ.ai/?url=api.datausa.io"),
    rev(body="is.gd/MaRound2019Xi991 and tinyurl.com/29qvy5md"),
]


class TestScan(unittest.TestCase):
    def setUp(self):
        self.scan = sec.scan_export(REVISIONS)

    def test_counts_every_revision(self):
        self.assertEqual(self.scan["revisions"], 5)

    def test_collects_hosts_from_bodies(self):
        self.assertIn("www.sec.gov", self.scan["hosts"])
        self.assertIn("api.datausa.io", self.scan["hosts"])
        self.assertIn("md.succ.ai", self.scan["hosts"])

    def test_plain_alias_is_captured(self):
        self.assertIn("massjson715", self.scan["aliases"]["vanderbi.lt"])

    def test_yourls_go_id_is_the_alias_not_the_script_name(self):
        aliases = self.scan["aliases"]["vanderbi.lt"]
        self.assertIn("bwkug", aliases)
        self.assertNotIn("yourls-go", aliases)
        self.assertNotIn("yourls-go.php", aliases)

    def test_scheme_less_shorteners_are_captured(self):
        self.assertIn("MaRound2019Xi991", self.scan["aliases"]["is.gd"])
        self.assertIn("29qvy5md", self.scan["aliases"]["tinyurl.com"])

    def test_placeholder_body_contributes_no_host(self):
        only_placeholder = sec.scan_export([rev(body="Describe the new page here.")])
        self.assertEqual(len(only_placeholder["hosts"]), 0)


class TestCrosscheck(unittest.TestCase):
    def setUp(self):
        self.scan = sec.scan_export(REVISIONS)
        self.links = [
            {"alias": "massjson715", "target": "https://www.sec.gov/files/county.json",
             "target_host": "sec.gov"},
            {"alias": "neverquoted", "target": "https://api.usa.gov/crime/fbi/cde",
             "target_host": "api.usa.gov"},
            {"alias": "alsonever", "target": "https://viz.aihw.gov.au/x",
             "target_host": "viz.aihw.gov.au"},
        ]

    def test_reports_seen_and_unseen_hosts(self):
        out = sec.crosscheck(self.scan, self.links)
        self.assertEqual(out["target_hosts"], 3)
        self.assertEqual(out["target_hosts_in_export"], 1)
        self.assertEqual(out["target_hosts_absent_from_export"], 2)
        self.assertIn("api.usa.gov", out["hosts_absent"])

    def test_www_prefix_does_not_split_a_host(self):
        """The export says www.sec.gov; the shortener says sec.gov. Same host."""
        out = sec.crosscheck(self.scan, self.links)
        self.assertIn("sec.gov", out["hosts_present"])

    def test_counts_aliases_the_export_cites(self):
        out = sec.crosscheck(self.scan, self.links)
        self.assertEqual(out["aliases_supplied"], 3)
        self.assertEqual(out["aliases_cited_in_export"], 1)
        self.assertEqual(out["aliases_cited"], ["massjson715"])


class TestLoadLinks(unittest.TestCase):
    def _write(self, name, text):
        path = Path(tempfile.mkdtemp()) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_json_list(self):
        p = self._write("l.json", json.dumps(
            [{"keyword": "abc", "url": "https://example.org/x"}]))
        rows = sec.load_links(p)
        self.assertEqual(rows[0]["alias"], "abc")
        self.assertEqual(rows[0]["target_host"], "example.org")

    def test_json_keyword_mapping(self):
        p = self._write("m.json", json.dumps(
            {"links": [{"shorturl": "https://vanderbi.lt/zz", "longurl":
                        "https://Data.Example.COM/a"}]}))
        rows = sec.load_links(p)
        self.assertEqual(rows[0]["alias"], "zz")
        self.assertEqual(rows[0]["target_host"], "data.example.com")

    def test_csv(self):
        p = self._write("l.csv", "alias,target\nq1,https://www.example.net/a\n")
        rows = sec.load_links(p)
        self.assertEqual(rows[0]["alias"], "q1")
        self.assertEqual(rows[0]["target_host"], "example.net")

    def test_trailing_plus_is_stripped_from_a_stats_page_alias(self):
        p = self._write("p.json", json.dumps(
            [{"keyword": "maagentxyz99999+", "url": "https://utoronto.ca/foo"}]))
        self.assertEqual(sec.load_links(p)[0]["alias"], "maagentxyz99999")


if __name__ == "__main__":
    unittest.main()
