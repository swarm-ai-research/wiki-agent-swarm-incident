import hashlib
import tempfile
import unittest
from pathlib import Path

import termina_vanderbilt_click_audit as audit


class TerminaVanderbiltClickAuditTests(unittest.TestCase):
    def test_claim_arithmetic_is_parsed_not_assumed(self):
        result = audit.parse_claim(
            "eight links gained 121 clicks between the september 4 kmad snapshots and september 6 captures; "
            "114 have no recorded referrer and seven have one; public statistics do not identify the callers"
        )
        self.assertEqual(result["links"], 8)
        self.assertEqual(result["referrer_partition_total"], 121)
        self.assertTrue(result["partition_matches_delta"])

    def test_missing_artifacts_produce_bounded_non_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = audit.build(evidence_root=root, recovery_report=root / "missing.json")
        self.assertEqual(report["local_disposition"], "not-independently-verifiable-from-held-files")
        self.assertTrue(all(not item["present"] for item in report["artifacts"]))
        self.assertIn("offline-only", report["network_policy"])

    def test_artifact_hash_is_checked_when_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "capture.json"
            path.write_text("{}")
            item = audit._artifact(
                {"id": "e", "kind": "primary", "publisher": "p", "path": "capture.json", "sha256": hashlib.sha256(b"{}").hexdigest(), "url": "https://invalid.example", "published": None, "retrieved_at": None},
                root,
            )
        self.assertTrue(item["present"])
        self.assertTrue(item["hash_matches"])


if __name__ == "__main__":
    unittest.main()
