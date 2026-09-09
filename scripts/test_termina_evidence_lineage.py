import sqlite3
import tempfile
import unittest
from pathlib import Path

import termina_evidence_lineage as lineage


class TerminaEvidenceLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = lineage.build(lineage.DB)
        cls.claims = {claim["id"]: claim for claim in cls.report["claims"]}

    def test_pinned_snapshot_is_fully_covered(self):
        self.assertEqual(self.report["counts"]["claims"], 237)
        self.assertEqual(self.report["counts"]["evidence"], 67)
        self.assertEqual(self.report["counts"]["lineage_edges"], 273)
        self.assertEqual(sum(self.report["counts"]["by_check_class"].values()), 237)
        self.assertEqual(len(self.report["lineage_edges"]), 273)

    def test_same_evidence_and_same_publisher_are_not_independent(self):
        self.assertEqual(self.claims["retrieval-venues-never-talk"]["check_class"], "same-evidence")
        self.assertIn("non-independent-check", self.claims["dse-talk-arrives-with-the-clock"]["flags"])
        self.assertEqual(self.claims["dse-talk-arrives-with-the-clock"]["check_class"], "same-publisher")

    def test_distinct_publishers_do_not_claim_proven_independence(self):
        claim = self.claims["kmad-probier-negative"]
        self.assertEqual(claim["check_class"], "cross-publisher")
        self.assertIn("publisher-distinct-check; independence-not-established", claim["flags"])

    def test_missing_maker_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "bad.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                "CREATE TABLE evidence (id TEXT, publisher TEXT, collection_id TEXT, path TEXT, kind TEXT);"
                "CREATE TABLE claim (id TEXT, made_by TEXT, checked_by TEXT, status TEXT);"
                "INSERT INTO claim VALUES ('bad', 'missing', NULL, 'reported');"
            )
            connection.close()
            with self.assertRaisesRegex(ValueError, "unresolved made_by"):
                lineage.build(database)

    def test_publisher_alias_is_narrow_and_explicit(self):
        self.assertEqual(lineage.canonical_publisher("ai-safety-lab (live pull)"), "ai-safety-lab")
        self.assertNotEqual(lineage.canonical_publisher("collusion.wiki"), lineage.canonical_publisher("AI-Safety-Commons"))


if __name__ == "__main__":
    unittest.main()
