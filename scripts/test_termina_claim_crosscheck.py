import unittest

import termina_claim_crosscheck as tc


class TerminaClaimCrosscheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = tc.build(tc.DB)
        cls.claims = {claim["id"]: claim for claim in cls.report["claims"]}

    def test_pinned_snapshot_claim_scope_is_complete(self):
        self.assertEqual(self.report["counts"]["claims"], 80)
        self.assertEqual(
            self.report["counts"]["by_termina_status"],
            {"contradicted": 1, "inferred": 50, "reported": 11, "verified": 18},
        )
        self.assertEqual(len(self.claims), 80)

    def test_direct_payload_disagreement_is_not_flattened(self):
        negative = self.claims["kmad-probier-negative"]
        self.assertEqual(negative["status"], "contradicted")
        self.assertEqual(negative["classification"], "conflicting")
        self.assertEqual(negative["archive_ref"], "analysis/field-evidence.md")
        self.assertEqual(
            self.claims["ours-probier-payload-is-data"]["classification"],
            "corroborated",
        )
        self.assertEqual(
            self.claims["rmn-re-shares-wiki-networks"]["classification"], "new"
        )

    def test_only_scan_verdict_transitions_are_material(self):
        scans = [
            claim for claim in self.report["claims"] if claim["id"].startswith("scan:")
        ]
        material = {claim["id"] for claim in scans if claim["material"]}
        self.assertEqual(
            material,
            {
                "scan:paste-linuxiarz:2026-09-08",
                "scan:vanderbilt:2026-09-08",
            },
        )
        self.assertTrue(
            all(
                claim["classification"] == "new"
                for claim in scans
                if claim["id"].endswith("2026-09-08")
            )
        )

    def test_evidence_ids_are_resolved_when_present(self):
        for claim in self.report["claims"]:
            if claim["made_by"]:
                self.assertIsNotNone(claim["made_by_evidence"])
            if claim["checked_by"]:
                self.assertIsNotNone(claim["checked_by_evidence"])


if __name__ == "__main__":
    unittest.main()
