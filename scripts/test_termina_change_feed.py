import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import termina_change_feed as feed


CLAIM_SCHEMA = """
CREATE TABLE claim (
 id TEXT PRIMARY KEY, subject_kind TEXT, subject_id TEXT, text TEXT,
 made_by TEXT, status TEXT, basis TEXT, checked_by TEXT, notes TEXT
);
CREATE TABLE evidence (id TEXT PRIMARY KEY, kind TEXT, publisher TEXT, path TEXT);
"""


def database(path, claims, evidence=()):
    connection = sqlite3.connect(path)
    connection.executescript(CLAIM_SCHEMA)
    connection.executemany("INSERT INTO claim VALUES (?,?,?,?,?,?,?,?,?)", claims)
    connection.executemany("INSERT INTO evidence VALUES (?,?,?,?)", evidence)
    connection.commit()
    connection.close()


def scan(venue, day, verdict, rows, status="inferred"):
    return (f"scan:{venue}:{day}", "venue", venue, f"scanner verdict {verdict} on {rows} rows: detail", "scan-evidence", status, "classifier", None, "")


def claim(identity, status="reported", made_by="e1", checked_by=None):
    return (identity, "incident", "i1", "claim text", made_by, status, "basis", checked_by, "")


class TerminaChangeFeedTests(unittest.TestCase):
    def compare(self, before_claims, after_claims, before_evidence=(), after_evidence=()):
        with tempfile.TemporaryDirectory() as directory:
            before, after = Path(directory) / "before.sqlite", Path(directory) / "after.sqlite"
            database(before, before_claims, before_evidence)
            database(after, after_claims, after_evidence)
            return feed.compare_databases(before, after)

    def test_unchanged_daily_scan_is_suppressed(self):
        report = self.compare([scan("wiki", "2026-09-07", "quiet", 12)], [scan("wiki", "2026-09-08", "quiet", 12)])
        self.assertTrue(all(count == 0 for count in report["summary"].values()))

    def test_verdict_and_row_coverage_transitions_remain_inferred(self):
        report = self.compare([scan("wiki", "2026-09-07", "quiet", 12)], [scan("wiki", "2026-09-08", "one gate", 20)])
        self.assertEqual(report["summary"]["scan_verdict_transitions"], 1)
        self.assertEqual(report["summary"]["scan_row_coverage_changes"], 1)
        self.assertEqual(report["events"]["scan_verdict_transitions"][0]["evidence_status"], "inferred")

    def test_claim_status_and_evidence_lineage_changes_are_emitted(self):
        evidence_before = [("e1", "secondary", "one", "old")]
        evidence_after = [("e1", "primary", "one", "new")]
        report = self.compare([claim("c1")], [claim("c1", "verified", checked_by="e1")], evidence_before, evidence_after)
        self.assertEqual(report["summary"]["claim_status_changes"], 1)
        self.assertEqual(report["summary"]["evidence_link_changes"], 1)
        self.assertEqual(report["summary"]["evidence_changes"], 1)

    def test_scan_claim_cannot_be_promoted_to_verified(self):
        with self.assertRaisesRegex(ValueError, "non-inferred status"):
            self.compare([scan("wiki", "2026-09-07", "quiet", 12)], [scan("wiki", "2026-09-08", "quiet", 12, "verified")])

    def test_directory_comparison_validates_both_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            before, after = Path(directory) / "before", Path(directory) / "after"
            before.mkdir(); after.mkdir()
            database(before / "incidents.sqlite", [claim("c1")], [("e1", "primary", "p", "x")])
            database(after / "incidents.sqlite", [claim("c1")], [("e1", "primary", "p", "x")])
            with patch.object(feed.verify_termina_snapshot, "verify", side_effect=[{"generated_at": "a"}, {"generated_at": "b"}]) as verify:
                report = feed.compare_snapshot_dirs(before, after)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(report["before"]["generated_at"], "a")


if __name__ == "__main__":
    unittest.main()
