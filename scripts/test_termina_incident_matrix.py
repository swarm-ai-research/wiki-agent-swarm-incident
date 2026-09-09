"""Offline tests for the generated Termina incident comparison."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import termina_incident_matrix as matrix


class TerminaIncidentMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = matrix.build(matrix.DB)
        cls.by_id = {row["id"]: row for row in cls.report["incidents"]}

    def test_all_incidents_and_requested_comparators_are_present(self):
        self.assertEqual(len(self.report["incidents"]), 6)
        self.assertEqual(
            set(self.by_id),
            {"artifactory-2026-05", "dsewiki-2026-05", "hf-2026-07",
             "modal-2026-07", "aisi-2026-07", "usemod-envelopes-2026-08"},
        )

    def test_status_categories_controls_and_evidence_are_preserved(self):
        dse = self.by_id["dsewiki-2026-05"]
        self.assertEqual(dse["severity"], "exposure")
        self.assertIn("egress-bypass", dse["categories"])
        self.assertEqual(len(dse["controls"]), 5)
        self.assertEqual(dse["evidence_id"], "collusion-wiki-report")
        self.assertEqual(dse["claim_statuses"]["reported"], 2)

    def test_missing_controls_are_not_rendered_as_no_controls(self):
        rendered = matrix.markdown(self.report)
        usemod_line = next(line for line in rendered.splitlines() if line.startswith("| `usemod-envelopes"))
        self.assertIn("not modelled (0 `defence` rows)", usemod_line)
        self.assertNotIn("no controls", usemod_line)

    def test_population_caveats_and_row_citations_are_rendered(self):
        rendered = matrix.markdown(self.report)
        self.assertIn("`collusion-two-populations` is **reported**", rendered)
        self.assertIn("not a population identity link", rendered)
        self.assertIn("`claim:aisi-fake-identities`", rendered)
        self.assertIn("`defence:hf-2026-07:sandbox-isolation`", rendered)

    def test_cli_outputs_are_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            json_out = Path(directory) / "matrix.json"
            markdown_out = Path(directory) / "matrix.md"
            with patch.object(
                matrix.argparse.ArgumentParser, "parse_args",
                return_value=matrix.argparse.Namespace(
                    database=matrix.DB, json=json_out, markdown=markdown_out
                ),
            ):
                self.assertEqual(matrix.main(), 0)
            self.assertEqual(json_out.read_text(), matrix.JSON_OUT.read_text())
            self.assertEqual(markdown_out.read_text(), matrix.MARKDOWN_OUT.read_text())


if __name__ == "__main__":
    unittest.main()
