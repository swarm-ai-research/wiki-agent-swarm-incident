import unittest
from pathlib import Path

import graph_clusters as clusters


class GraphClusterViewTests(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "nodes": [
                {"id": "page", "type": "wikipage", "label": "Page"},
                {"id": "wiki", "type": "wiki", "label": "Wiki"},
                {"id": "campaign", "type": "campaign", "label": "Campaign"},
                {"id": "cluster", "type": "cluster", "label": "Cluster"},
            ],
            "links": [
                {"source": "page", "target": "wiki", "src": "atlas"},
                {"source": "campaign", "target": "cluster", "src": "termina-db"},
                {"source": "cluster", "target": "wiki", "src": "termina-db"},
            ],
        }

    def test_operational_build_excludes_provenance_nodes_and_edges(self):
        nodes, graph = clusters.build(self.graph)
        self.assertEqual(set(nodes), {"page", "wiki"})
        self.assertEqual(set(graph.edges()), {("page", "wiki")})

    def test_provenance_build_retains_complete_graph(self):
        nodes, graph = clusters.build(self.graph, include_provenance=True)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(graph.number_of_edges(), 3)

    def test_both_summaries_are_generated(self):
        summaries = clusters.summarize_views(self.graph)
        self.assertEqual(set(summaries), {"operational", "provenance"})
        self.assertEqual(summaries["operational"]["components"], [2])
        self.assertEqual(summaries["provenance"]["components"], [4])

    def test_real_atlas_default_excludes_only_termina_summary_layer(self):
        page = Path(__file__).resolve().parents[1] / "graph.html"
        graph = clusters.load_graph(page.read_text())
        operational_nodes, operational = clusters.build(graph)
        all_nodes, complete = clusters.build(graph, include_provenance=True)
        self.assertEqual(len(all_nodes) - len(operational_nodes), 10)
        self.assertEqual(complete.number_of_edges() - operational.number_of_edges(), 56)
        self.assertFalse(any(clusters.is_provenance_node(node) for node in operational_nodes.values()))

    def test_atlas_ui_rebuilds_paths_layout_and_clusters_by_view(self):
        page = (Path(__file__).resolve().parents[1] / "graph.html").read_text()
        self.assertIn('id="viewSel"', page)
        self.assertIn("activeNodes=nodes.filter", page)
        self.assertIn("activeLinks=links.filter", page)
        self.assertIn("for(const nx of (adj.get(c)||[]))", page)
        self.assertIn("const summary=CLUSTERS[viewMode]||CLUSTERS", page)
        self.assertIn("function updateLegendCounts()", page)
        self.assertIn("updateCounts(); updateLegendCounts(); syncPathOptions()", page)


if __name__ == "__main__":
    unittest.main()
