import json
import tempfile
import unittest
from pathlib import Path

import swarm_detection_stress_test as stress


ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "data/swarm_detection_spec_v1.json").read_text())


class GateTests(unittest.TestCase):
    def test_behavior_saturation_cannot_reach_swarm_tier(self):
        active = {
            signal_id: stress.signal("surface_history", "inferred")
            for signal_id in SPEC["non_attribution_signals"]
        }
        active["target_convergence"] = stress.signal("held_artifact", "read")
        active["population_relocation"] = stress.signal(
            "external_service_record", "inferred"
        )
        result = stress.classify(
            SPEC,
            active,
            ("surface_history", "held_artifact", "external_service_record"),
        )
        self.assertEqual(result["highest_tier"], "S2_correlated_campaign")
        suspected = next(
            item
            for item in result["evaluations"]
            if item["tier"] == "S3_suspected_coordinated_swarm"
        )
        self.assertIn("signal:identity_multiplicity", suspected["blockers"])
        self.assertIn("family:coordination", suspected["blockers"])

    def test_reported_direct_handoff_cannot_confirm(self):
        active = {
            "volume_burst": stress.signal("surface_history", "export"),
            "target_convergence": stress.signal("held_artifact", "read"),
            "identity_multiplicity": stress.signal("held_artifact", "read"),
            "read_write_handoff": stress.signal("surface_history", "reported"),
        }
        result = stress.classify(SPEC, active, ("surface_history", "held_artifact"))
        self.assertEqual(result["highest_tier"], "S3_suspected_coordinated_swarm")
        confirmed = next(
            item
            for item in result["evaluations"]
            if item["tier"] == "S4_confirmed_coordinated_swarm"
        )
        self.assertIn("primary_status:read_write_handoff", confirmed["blockers"])

    def test_unrelated_evidence_cannot_launder_gate_independence(self):
        active = {
            "volume_burst": stress.signal("surface_history", "export"),
            "target_convergence": stress.signal("surface_history", "read"),
            "identity_multiplicity": stress.signal("held_artifact", "read"),
            "read_write_handoff": stress.signal("held_artifact", "read"),
            "external_consequence": stress.signal("operator_confirmation", "verified"),
        }
        result = stress.classify(SPEC, active)
        suspected = next(
            item
            for item in result["evaluations"]
            if item["tier"] == "S3_suspected_coordinated_swarm"
        )
        self.assertFalse(suspected["passed"])
        self.assertEqual(suspected["gate_evidence_classes"], ["held_artifact"])
        self.assertIn("evidence_classes:2", suspected["blockers"])


class HeldDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = stress.build_report(ROOT)

    def test_wiki_reaches_campaign_but_not_swarm_gate(self):
        case = self.report["wiki_incident"]
        self.assertEqual(
            case["classification"]["highest_tier"], "S2_correlated_campaign"
        )
        self.assertEqual(case["metrics"]["peak_daily_writes"], 6652)
        self.assertEqual(case["metrics"]["supported_runs"], 298)
        self.assertEqual(case["metrics"]["largest_supported_run_family"], 58)

    def test_removing_run_map_falls_to_automation(self):
        ablation = self.report["wiki_incident"]["ablations"]["remove_run_map"]
        self.assertEqual(ablation["highest_tier"], "S1_automation_anomaly")

    def test_behavior_only_ablation_stops_before_swarm(self):
        ablation = self.report["wiki_incident"]["ablations"][
            "saturate_behavior_without_multiplicity_or_coordination"
        ]
        self.assertEqual(ablation["highest_tier"], "S2_correlated_campaign")

    def test_termina_scanner_swarm_labels_do_not_auto_promote(self):
        case = self.report["termina_scanner_crosscheck"]
        self.assertEqual(case["latest_scanned_venues"], 54)
        self.assertEqual(case["upstream_swarm_labels"], 4)
        self.assertEqual(case["v1_swarm_tiers"], 0)

    def test_clean_human_controls_have_no_alerts(self):
        controls = self.report["termina_scanner_crosscheck"]["human_default_candidates"]
        self.assertEqual(controls["venues"], 21)
        self.assertEqual(controls["incident_or_campaign_linked"], 13)
        self.assertEqual(controls["clean_unlinked"], 8)
        self.assertEqual(controls["upstream_alerts_all"], 5)
        self.assertEqual(controls["upstream_alerts_clean"], 0)
        self.assertEqual(controls["v1_swarm_tiers_clean"], 0)

    def test_report_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            rendered = json.dumps(self.report, indent=2, sort_keys=True) + "\n"
            first.write_text(rendered)
            second.write_text(
                json.dumps(stress.build_report(ROOT), indent=2, sort_keys=True) + "\n"
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_all_held_data_invariants_pass(self):
        self.assertTrue(self.report["stress_test_passed"])
        self.assertTrue(all(self.report["invariants"].values()))


class MythosControlTests(unittest.TestCase):
    @unittest.skipUnless(
        Path("/tmp/mythos-5-incident-transcript/transcript.jsonl").exists(),
        "local content-controlled Mythos transcript is unavailable",
    )
    def test_single_agent_control_stops_at_automation(self):
        case = stress.mythos_case(
            SPEC, Path("/tmp/mythos-5-incident-transcript/transcript.jsonl")
        )
        self.assertEqual(
            case["classification"]["highest_tier"], "S1_automation_anomaly"
        )
        self.assertFalse(case["multiplicity_signal_active"])
        self.assertFalse(case["content_emitted"])
        self.assertEqual(case["metrics"]["longest_same_tool_run"], 36)


if __name__ == "__main__":
    unittest.main()
