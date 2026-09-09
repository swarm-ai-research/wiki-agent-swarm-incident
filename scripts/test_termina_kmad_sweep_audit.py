import tempfile
import unittest
from pathlib import Path

import termina_kmad_sweep_audit as audit


class TerminaKmadSweepAuditTests(unittest.TestCase):
    def test_narrative_only_fixture_is_not_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("A sweep covered 6,271 candidates from WikiIndex.")
            result = audit.audit_repository(root)
        self.assertEqual(result["tracked_files"], 1)
        self.assertFalse(result["reproducible_from_repository"])
        self.assertEqual(result["implementation_or_result_files"], [])

    def test_machine_readable_ledger_changes_inventory_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "wiki-sweep-results.jsonl").write_text('{"source":"WikiIndex","targets":6271}\n')
            result = audit.audit_repository(root)
        self.assertTrue(result["reproducible_from_repository"])
        self.assertEqual(result["implementation_or_result_files"], ["wiki-sweep-results.jsonl"])

    def test_real_claim_is_retained_as_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("6,271 wiki installs")
            report = audit.build(root)
        self.assertEqual(report["termina_status"], "reported")
        self.assertEqual(report["local_disposition"], "reported-not-reproducible")
        self.assertEqual(report["published_narrative"]["candidate_denominator"], 6271)


if __name__ == "__main__":
    unittest.main()
