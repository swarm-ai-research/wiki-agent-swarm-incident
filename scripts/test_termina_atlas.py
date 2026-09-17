import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import atlas_augment as aa  # noqa: E402


class TerminaAtlasTests(unittest.TestCase):
    def test_full_database_adds_compact_provenance_layer_idempotently(self):
        graph = {
            "nodes": [
                {"id": "wiki:dse", "label": "dse", "type": "wiki", "detail": ""},
            ],
            "links": [],
            "relmeta": {},
        }
        database = ROOT / "data" / "termina" / "incidents.sqlite"
        stats = aa.termina_database_edges(graph, database)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertIn("incident:dsewiki-2026-05", nodes)
        self.assertIn("campaign:swarm-cohort", nodes)
        self.assertIn("campaign:swarm-retrieval", nodes)
        self.assertIn("cluster:datausa-sequence", nodes)
        self.assertIn("1 verified", nodes["campaign:swarm-cohort"]["detail"])
        self.assertEqual(stats["clusters_new"], 7)
        self.assertEqual(stats["record_edges_summarized"], 16073)
        links = {
            (link["source"], link["target"], link["rt"])
            for link in graph["links"]
        }
        self.assertIn(
            ("incident:dsewiki-2026-05", "campaign:swarm-cohort", "contains"),
            links,
        )
        self.assertIn(
            ("campaign:swarm-cohort", "cluster:datausa-sequence", "contains"),
            links,
        )
        self.assertIn(("cluster:datausa-sequence", "wiki:dse", "observed_at"), links)

        again = aa.termina_database_edges(graph, database)
        self.assertEqual(again.get("incident_campaign_links", 0), 0)
        self.assertEqual(again.get("campaign_cluster_links", 0), 0)
        self.assertEqual(again.get("cluster_venue_links", 0), 0)


if __name__ == "__main__":
    unittest.main()
