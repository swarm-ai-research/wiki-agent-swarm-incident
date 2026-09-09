import unittest

import termina_campaign_analysis as analysis


class TerminaCampaignAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analysis.build()
        cls.campaigns = {row["id"]: row for row in cls.report["campaigns"]}

    def test_all_campaign_kinds_are_retained(self):
        self.assertEqual(len(self.campaigns), 8)
        self.assertEqual(
            {row["kind"] for row in self.report["campaigns"]},
            {"swarm", "run", "research-environment", "visitors"},
        )

    def test_observations_are_not_reported_as_writes(self):
        self.assertEqual(self.campaigns["swarm-cohort"]["observations"], 14189)
        self.assertEqual(self.campaigns["swarm-cohort"]["estimated_writes"], 5236)
        self.assertEqual(self.campaigns["swarm-retrieval"]["estimated_writes"], 7136)
        self.assertEqual(self.campaigns["usemod-fleet"]["observations"], 18)
        self.assertEqual(self.campaigns["usemod-fleet"]["estimated_writes"], 6)

    def test_paste_only_campaign_is_one_row_per_write(self):
        xinzhai = self.campaigns["xinzhai-2026-07"]
        self.assertEqual(xinzhai["observations"], xinzhai["estimated_writes"])
        self.assertEqual(xinzhai["observation_kinds"], {"paste": 3574})

    def test_write_bounds_are_ordered_and_central_is_backward_compatible(self):
        for campaign in self.report["campaigns"]:
            bounds = campaign["write_bounds"]
            self.assertLessEqual(bounds["lower"], bounds["central"])
            self.assertLessEqual(bounds["central"], bounds["upper"])
            self.assertEqual(bounds["central"], campaign["estimated_writes"])
            self.assertEqual(
                {row["venue_id"] for row in campaign["venue_write_bounds"]},
                set(campaign["venue_ids"]),
            )

    def test_usemod_bounds_expose_recent_changes_overlap(self):
        self.assertEqual(
            self.campaigns["usemod-fleet"]["write_bounds"],
            {"lower": 6, "central": 6, "upper": 18},
        )

    def test_scanner_status_and_missing_coverage_stay_explicit(self):
        for campaign in self.report["campaigns"]:
            self.assertLessEqual(campaign["scan_coverage"], campaign["venues"])
            self.assertTrue(all(row["status"] == "inferred" for row in campaign["latest_scans"]))
            if campaign["observations"]:
                self.assertEqual(campaign["record_statuses"], {"live": campaign["observations"]})
        controls = self.report["human_control_scans"]
        self.assertLess(controls["covered_venues"], controls["default_human_venues"])
        self.assertEqual(controls["status_counts"], {"inferred": controls["covered_venues"]})

    def test_generation_metadata_is_pinned_to_the_snapshot(self):
        self.assertEqual(self.report["schema_version"], "9")
        self.assertEqual(self.report["generated_on"], "2026-09-08")
        self.assertTrue(self.report["snapshot_generated_at"].startswith("2026-09-08T"))


if __name__ == "__main__":
    unittest.main()
