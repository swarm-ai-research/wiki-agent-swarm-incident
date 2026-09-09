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
        nodes, graph = clusters.build(self.graph, include_provenance=False)
        self.assertEqual(set(nodes), {"page", "wiki"})
        self.assertEqual(set(graph.edges()), {("page", "wiki")})

    def test_atlas_build_retains_complete_graph(self):
        nodes, graph = clusters.build(self.graph)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(graph.number_of_edges(), 3)

    def test_both_summaries_are_generated(self):
        summaries = clusters.summarize_views(self.graph)
        self.assertEqual(set(summaries), {"atlas", "operational"})
        self.assertEqual(summaries["operational"]["components"], [2])
        self.assertEqual(summaries["atlas"]["components"], [4])

    def test_relay_and_provenance_axes_compose(self):
        relay = {
            "nodes": [{"id": "run", "type": "run", "label": "Run"}],
            "links": [{"source": "run", "target": "page", "src": "relay"}],
        }
        summaries = clusters.summarize_views(self.graph, relay)
        self.assertEqual(
            set(summaries),
            {"atlas", "operational", "combined", "combined_operational"},
        )
        # the relay layer survives the provenance filter; the termina-db layer does not
        self.assertEqual(summaries["combined_operational"]["components"], [3])
        self.assertEqual(summaries["combined"]["components"], [5])

    def test_real_atlas_operational_view_drops_only_the_termina_layer(self):
        """Against the shipped atlas, not a fixture: the delta is exactly the
        termina layer, counted from the data rather than pinned to a constant
        that goes stale every time the atlas grows."""
        page = Path(__file__).resolve().parents[1] / "graph.html"
        graph = clusters.load_graph(page.read_text())
        operational_nodes, operational = clusters.build(graph, include_provenance=False)
        all_nodes, complete = clusters.build(graph)

        expected_nodes = sum(1 for n in graph["nodes"] if clusters.is_provenance_node(n))
        expected_edges = len({
            frozenset((l["source"], l["target"]))
            for l in graph["links"]
            if clusters.is_provenance_link(l)
            and l["source"] in all_nodes and l["target"] in all_nodes
        })
        self.assertEqual(len(all_nodes) - len(operational_nodes), expected_nodes)
        self.assertEqual(complete.number_of_edges() - operational.number_of_edges(), expected_edges)
        self.assertFalse(any(clusters.is_provenance_node(node) for node in operational_nodes.values()))
        # the layer is load-bearing on the current atlas: without it the graph fragments
        self.assertGreater(expected_edges, 0)


if __name__ == "__main__":
    unittest.main()
