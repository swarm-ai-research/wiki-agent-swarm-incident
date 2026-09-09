import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetrWhistleblowingAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(
            (ROOT / "data/metr_whistleblowing_audit_2026-09-08.json").read_text()
        )

    def test_primary_range_is_not_flattened_to_exact_six(self):
        findings = self.audit["primary_findings"]
        disposition = self.audit["disposition"]
        self.assertEqual(findings["actual_example_range"], [3, 6])
        self.assertEqual(findings["raw_hits"], 10)
        self.assertTrue(findings["raw_hits_include_false_positives"])
        self.assertFalse(disposition["exact_six_supported"])
        self.assertTrue(disposition["six_as_upper_bound_supported"])

    def test_denominator_and_zero_action_are_preserved(self):
        self.assertEqual(
            self.audit["primary_findings"]["transcript_population_approximate"],
            1300,
        )
        self.assertEqual(self.audit["primary_findings"]["acted_on_alerting"], 0)
        self.assertTrue(self.audit["disposition"]["denominator_supported"])
        self.assertTrue(self.audit["disposition"]["zero_acted_supported"])

    def test_human_readable_crosscheck_uses_the_range(self):
        crosscheck = (ROOT / "analysis/zvi-hf-postmortem-crosscheck.md").read_text()
        self.assertIn("3-6 actual examples", crosscheck)
        self.assertIn("6 is an upper bound, not an exact count", crosscheck)
        self.assertNotIn("unverified against the METR PDF", crosscheck)


if __name__ == "__main__":
    unittest.main()
