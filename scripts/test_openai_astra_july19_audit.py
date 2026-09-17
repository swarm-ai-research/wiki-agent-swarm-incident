import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OpenAIAstraJuly19AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(
            (ROOT / "data/openai_astra_july19_audit_2026-09-08.json").read_text()
        )

    def test_model_identity_is_not_flattened(self):
        parts = self.audit["claim_components"]
        self.assertEqual(parts["same_family_as_astra"], "supported")
        self.assertEqual(parts["model_was_astra"], "contradicted")
        self.assertEqual(parts["distinct_post_training"], "supported")
        self.assertEqual(parts["more_capable_than_prior_model"], "unsupported")

    def test_event_and_internal_compromise_are_preserved(self):
        parts = self.audit["claim_components"]
        self.assertEqual(parts["july_19"], "supported")
        self.assertEqual(parts["separate_evaluation_run"], "supported")
        self.assertEqual(parts["internal_infrastructure_compromise"], "supported")
        self.assertIn("Kubernetes cluster-admin", self.audit["primary_event"]["access"])

    def test_crosscheck_no_longer_calls_the_claim_unresolved(self):
        text = (ROOT / "analysis/zvi-hf-postmortem-crosscheck.md").read_text()
        self.assertIn('description **"even more capable" is not**', text)
        self.assertIn("distinct\nmodel with different post-training", text)
        self.assertNotIn("single-source, unresolved", text)


if __name__ == "__main__":
    unittest.main()
