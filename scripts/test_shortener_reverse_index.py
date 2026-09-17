import hashlib
import json
import tempfile
import unittest
from datetime import date
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
            "generated_on": "2026-09-08",
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


class ShortenerTruncationTests(unittest.TestCase):
    def _chain_fixture(self, directory, length):
        """A single flow chain of `length` edges off one shortener."""
        nodes = [{"id": "short:a/one", "label": "a/one", "type": "shortener"}]
        links = []
        previous = "short:a/one"
        for step in range(length):
            node_id = f"hop:{step}"
            nodes.append({"id": node_id, "label": str(step), "type": "proxy"})
            links.append({"source": previous, "target": node_id, "rt": "proxies"})
            previous = node_id
        graph_path = Path(directory) / "graph.html"
        graph_path.write_text(
            f"<script>\nconst GRAPH = {json.dumps({'nodes': nodes, 'links': links})};\n</script>\n"
        )
        ledger_path = Path(directory) / "ledger.json"
        ledger_path.write_text(json.dumps({
            "generated_on": "2026-09-08", "codes": [], "discovered_hops": [],
        }))
        return graph_path, ledger_path

    def test_chain_within_max_depth_reports_no_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._chain_fixture(directory, reverse.MAX_DEPTH)
            report = reverse.build(graph, ledger)
        self.assertEqual(report["counts"]["paths_truncated_at_max_depth"], 0)
        self.assertEqual(report["counts"]["destinations"], reverse.MAX_DEPTH)

    def test_chain_deeper_than_max_depth_counts_the_dropped_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._chain_fixture(directory, reverse.MAX_DEPTH + 3)
            report = reverse.build(graph, ledger)
        # The three hops past the limit are dropped, and the count says so
        # rather than letting truncation read as non-existence.
        self.assertEqual(report["counts"]["destinations"], reverse.MAX_DEPTH)
        self.assertEqual(report["counts"]["paths_truncated_at_max_depth"], 1)

    def test_truncation_counter_ignores_nodes_already_reached(self):
        """A suppressed edge back to an already-found node loses nothing."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._chain_fixture(directory, reverse.MAX_DEPTH + 1)
            text = graph.read_text()
            # point the over-depth hop back at a node found at depth 1
            graph.write_text(text.replace(
                f'"source": "hop:{reverse.MAX_DEPTH - 1}", "target": "hop:{reverse.MAX_DEPTH}"',
                f'"source": "hop:{reverse.MAX_DEPTH - 1}", "target": "hop:0"',
            ))
            report = reverse.build(graph, ledger)
        self.assertEqual(report["counts"]["paths_truncated_at_max_depth"], 0)


class ShortenerDeterminismTests(unittest.TestCase):
    def _fixtures(self, directory):
        return ShortenerReverseIndexTests()._fixtures(directory)

    def test_stamp_is_inherited_from_the_inputs_not_the_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            report = reverse.build(graph, ledger)
        self.assertEqual(report["generated_on"], "2026-09-08")
        self.assertNotEqual(report["generated_on"], date.today().isoformat())

    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            first = reverse.build(graph, ledger)
            second = reverse.build(graph, ledger)
        self.assertEqual(
            json.dumps(first, indent=2, sort_keys=True),
            json.dumps(second, indent=2, sort_keys=True),
        )

    def test_explicit_stamp_overrides_the_inherited_one(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            report = reverse.build(graph, ledger, "2026-01-01")
        self.assertEqual(report["generated_on"], "2026-01-01")

    def test_ledger_without_a_stamp_refuses_to_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            payload = json.loads(ledger.read_text())
            del payload["generated_on"]
            ledger.write_text(json.dumps(payload))
            with self.assertRaises(ValueError) as caught:
                reverse.build(graph, ledger)
        self.assertIn("--generated-on", str(caught.exception))

    def test_input_hashes_pin_what_was_read(self):
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = self._fixtures(directory)
            report = reverse.build(graph, ledger)
            expected = hashlib.sha256(graph.read_bytes()).hexdigest()
        self.assertEqual(report["input_sha256"]["graph"], expected)


if __name__ == "__main__":
    unittest.main()
