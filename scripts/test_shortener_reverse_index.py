import json
import tempfile
import unittest
from pathlib import Path

import shortener_reverse_index as reverse


class ShortenerReverseIndexTests(unittest.TestCase):
    def _fixtures(self, directory):
        graph = {
            "nodes": [
                {"id": "short:a/one", "label": "a/one", "type": "shortener"},
                {"id": "short:b/two", "label": "b/two", "type": "shortener"},
                {"id": "proxy:p", "label": "p", "type": "proxy"},
                {"id": "end:x", "label": "x", "type": "endpoint"},
                {"id": "end:y", "label": "y", "type": "endpoint"},
            ],
            "links": [
                {"source": "short:a/one", "target": "short:b/two", "rt": "resolves_to"},
                {"source": "short:b/two", "target": "proxy:p", "rt": "resolves_to"},
                {"source": "proxy:p", "target": "end:x", "rt": "proxies"},
                {"source": "proxy:p", "target": "end:y", "rt": "proxies"},
            ],
        }
        graph_path = Path(directory) / "graph.html"
        graph_path.write_text(f"<script>\nconst GRAPH = {json.dumps(graph)};\n</script>\n")
        ledger_path = Path(directory) / "ledger.json"
        ledger_path.write_text(json.dumps({
            "codes": [{"code": "c/three", "disposition": "no-archive-capture-found", "resolution": None}],
            "discovered_hops": [],
        }))
        return graph_path, ledger_path

    def test_build_separates_observed_chains_from_proxy_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            report = reverse.build(graph, ledger)
        by_target = {row["target_id"]: row for row in report["destinations"]}
        nested = next(row for row in by_target["proxy:p"]["matches"] if row["short_code"] == "a/one")
        candidate = next(row for row in by_target["end:x"]["matches"] if row["short_code"] == "a/one")
        self.assertEqual(nested["relation"], "nested")
        self.assertEqual(nested["confidence"], "observed-chain")
        self.assertEqual(candidate["relation"], "reachable")
        self.assertEqual(candidate["confidence"], "topology-candidate")
        self.assertIn("c/three", report["unresolved_codes"])

    def test_csv_has_destination_keyed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            text = reverse.csv_text(reverse.build(graph, ledger))
        self.assertIn("target_id,target_label,target_type,short_code", text)
        self.assertIn("end:x,x,endpoint,a/one,reachable,topology-candidate", text)


class ShortenerCodeKeyTests(unittest.TestCase):
    def test_host_is_case_insensitive_but_path_is_not(self):
        self.assertEqual(reverse.normalize_code("IS.GD/AbC"), "is.gd/AbC")
        self.assertNotEqual(
            reverse.normalize_code("is.gd/AbC"), reverse.normalize_code("is.gd/abc")
        )

    def test_distinct_codes_differing_only_by_path_case_stay_separate(self):
        ledger = {
            "codes": [
                {"code": "is.gd/AbC", "disposition": "archived-target-recovered"},
                {"code": "is.gd/abc", "disposition": "no-archive-capture-found"},
            ],
            "discovered_hops": [],
        }
        indexed = reverse.index_ledger(ledger)
        self.assertEqual(len(indexed), 2)
        self.assertEqual(
            indexed["is.gd/AbC"]["disposition"], "archived-target-recovered"
        )
        self.assertEqual(
            indexed["is.gd/abc"]["disposition"], "no-archive-capture-found"
        )

    def test_host_case_variants_of_one_code_are_a_collision(self):
        ledger = {
            "codes": [
                {"code": "IS.GD/abc", "disposition": "archived-target-recovered"},
                {"code": "is.gd/abc", "disposition": "no-archive-capture-found"},
            ],
            "discovered_hops": [],
        }
        with self.assertRaises(ValueError) as caught:
            reverse.index_ledger(ledger)
        self.assertIn("collision", str(caught.exception))

    def test_repeated_identical_code_is_not_a_collision(self):
        ledger = {
            "codes": [
                {"code": "is.gd/abc", "disposition": "no-archive-capture-found"},
                {"code": "is.gd/abc", "disposition": "archived-target-recovered"},
            ],
            "discovered_hops": [{"code": "is.gd/abc", "disposition": "ignored"}],
        }
        indexed = reverse.index_ledger(ledger)
        self.assertEqual(len(indexed), 1)
        # later rows in "codes" win; discovered_hops only fill gaps
        self.assertEqual(
            indexed["is.gd/abc"]["disposition"], "archived-target-recovered"
        )


class ShortenerCountsTests(unittest.TestCase):
    def test_counts_expose_the_observed_versus_candidate_split(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = ShortenerReverseIndexTests()._fixtures(directory)
            report = reverse.build(graph, ledger)
        split = report["counts"]["relations_by_confidence"]
        self.assertEqual(set(split), {"observed", "observed-chain", "topology-candidate"})
        self.assertEqual(report["counts"]["relations_total"], sum(split.values()))
        # fixture: a/one -> b/two and b/two -> proxy:p are the two observed
        # edges; a/one -> proxy:p is the one observed chain; both codes reach
        # end:x and end:y through the shared proxy, so four candidates.
        self.assertEqual(split["observed"], 2)
        self.assertEqual(split["observed-chain"], 1)
        self.assertEqual(split["topology-candidate"], 4)


if __name__ == "__main__":
    unittest.main()
