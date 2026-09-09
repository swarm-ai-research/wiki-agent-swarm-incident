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


if __name__ == "__main__":
    unittest.main()
