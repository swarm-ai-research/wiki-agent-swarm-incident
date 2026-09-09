import unittest

import termina_network_mixture as mixture


class TerminaNetworkMixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = mixture.build(mixture.DB)

    def test_ipv4_prefix_normalization(self):
        self.assertEqual(mixture.ipv4_16("20.165"), "20.165")
        self.assertEqual(mixture.ipv4_16("20.165.4.x"), "20.165")
        with self.assertRaises(ValueError):
            mixture.ipv4_16("999.1.x.x")

    def test_three_to_one_categories_reproduce_claim_arithmetic(self):
        wiki = self.report["wiki_prefixes"]
        self.assertEqual(wiki["union"], 189)
        self.assertEqual(
            wiki["categories_3_to_1"],
            {"a-talk-leaning": 46, "b-tools-leaning": 12, "balanced-mixed": 131},
        )

    def test_both_is_not_the_same_as_balanced(self):
        self.assertEqual(self.report["wiki_prefixes"]["both_any_ratio"], 137)
        self.assertEqual(self.report["terminology_correction"]["balanced_mixed_at_3_to_1"], 131)

    def test_rmn_join_accounts_for_unmatched_rows(self):
        rmn = self.report["rmn_re"]
        self.assertEqual(rmn["records_in_window"], 543)
        self.assertEqual(
            rmn["by_prefix_category"],
            {"a-talk-leaning": 4, "b-tools-leaning": 5, "balanced-mixed": 485, "invalid-or-redacted": 1, "unmatched": 48},
        )
        self.assertEqual(sum(rmn["by_prefix_category"].values()), 543)

    def test_result_remains_inferred(self):
        self.assertEqual(self.report["evidence_status"], "inferred")


if __name__ == "__main__":
    unittest.main()
