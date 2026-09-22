import json
import unittest
from pathlib import Path

import mechanics_of_a_swarm_crosscheck as crosscheck


class OccupancyInversionTests(unittest.TestCase):
    def test_inversion_is_the_left_inverse_of_the_occupancy_curve(self):
        for draws in (50, 275, 876):
            distinct = crosscheck.expected_distinct(draws)
            self.assertAlmostEqual(crosscheck.invert_occupancy(distinct), draws, places=6)

    def test_inversion_rejects_marks_outside_the_marker_space(self):
        for distinct in (0, crosscheck.MARKER_SPACE, crosscheck.MARKER_SPACE + 1):
            with self.assertRaises(ValueError):
                crosscheck.invert_occupancy(distinct)

    def test_published_canonical_estimate_follows_from_its_own_observed_d(self):
        self.assertEqual(round(crosscheck.invert_occupancy(332)), 876)


class DailyComparisonTests(unittest.TestCase):
    def test_disagreement_is_reported_per_day_and_field(self):
        repo = [{"date": "2026-06-18", "saves_dse": "5884", "saves_probier": "651",
                 "saves_fractal": "8", "saves_dorfwiki": "0", "deletes": "25"}]
        archive = [{"d": "2026-06-18", "dse": 5884, "probier": 650, "fractal": 8,
                    "wiki4d": 0, "other": 0, "del": 24}]
        result = crosscheck.compare_daily(repo, archive)
        self.assertEqual(result["days_compared"], 1)
        self.assertEqual({m["field"] for m in result["mismatches"]}, {"probier", "del"})

    def test_days_absent_from_the_archive_series_are_not_silently_dropped(self):
        repo = [{"date": "2026-05-24", "saves_dse": "16", "saves_probier": "7",
                 "saves_fractal": "12", "saves_dorfwiki": "0", "deletes": "0"}]
        result = crosscheck.compare_daily(repo, [])
        self.assertEqual(result["days_in_repository_only"], ["2026-05-24"])
        self.assertEqual(result["mismatches"], [])


class MarkerCalibrationTests(unittest.TestCase):
    def test_unsupported_runs_and_markerless_names_are_separated(self):
        payload = {"source": "test", "runs": {
            "a/1": {"name": "Nov22OECDScout", "supported": True},
            "b/1": {"name": "Nov22OtherScout", "supported": True},
            "c/1": {"name": "Jul99BadDay", "supported": True},
            "d/1": {"name": "Dec01Ignored", "supported": False},
        }}
        result = crosscheck.marker_calibration(payload)
        self.assertEqual(result["supported_runs"], 3)
        self.assertEqual(result["runs_with_calendar_valid_marker"], 2)
        self.assertEqual(result["runs_without_marker"], 1)
        self.assertEqual(result["distinct_markers"], 1)


class SavedReportTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.report = json.loads(
            (root / "data/mechanics_of_a_swarm_crosscheck_2026-09-14.json").read_text())

    def test_daily_series_agrees_on_every_compared_day(self):
        daily = self.report["daily_series"]
        self.assertEqual(daily["days_compared"], 44)
        self.assertEqual(daily["mismatches"], [])
        self.assertEqual(daily["days_in_repository_only"], [])
        self.assertTrue(all(daily["controls_present"].values()))
        self.assertEqual(daily["totals"]["repository"]["dse"], 13403)
        self.assertEqual(daily["totals"]["repository"]["del"], 5217)

    def test_archive_series_carries_wikis_outside_the_export_cut(self):
        outside = self.report["daily_series"]["totals"]["archive_outside_export_cut"]
        self.assertEqual(outside, {"wiki4d": 235, "other": 187})

    def test_population_estimates_reproduce_to_within_a_tenth_of_a_percent(self):
        self.assertLess(self.report["population_estimator"]["max_relative_difference"], 0.001)

    def test_repository_pins_the_export_without_re_hosting_it(self):
        source = self.report["source"]
        self.assertEqual(source["export_files_present"], [])
        self.assertEqual(len(source["export_files_pinned_by_hash"]), 5)
        self.assertEqual(source["tag"], "v1.0.1")

    def test_marker_model_is_not_rejected_on_the_held_run_identities(self):
        cal = self.report["marker_calibration"]
        self.assertFalse(cal["uniform_by_month_length_rejected_at_5pct"])
        self.assertLess(abs(cal["implied_minus_audited_relative"]), 0.05)

    def test_page_anchor_agrees_with_the_archive_dating(self):
        self.assertTrue(all(anchor["agrees"] for anchor in self.report["page_anchors"]))


if __name__ == "__main__":
    unittest.main()
