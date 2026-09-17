"""Tests for scripts/graph_clusters.py.

The script rewrites a ``const CLUSTERS = ...;`` line inside the committed
graph.html by regex, so the cases that matter are: the GRAPH literal survives
untouched, a re-run is idempotent, and cluster names carrying backslashes do
not get interpreted as regex replacement templates.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

# graph_clusters needs networkx, which CI does not install (the workflow runs
# the suite on a bare stdlib Python). Skip rather than fail there, the same way
# the teardown tests skip without their bundle.
try:
    import graph_clusters as gc  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - depends on the env
    gc = None
    _MISSING = exc.name
else:
    _MISSING = None


def page(graph, clusters_line=True):
    """A minimal graph.html shaped like the real one."""
    lines = [
        "<!doctype html><html><body><div id='clusters'></div><script>",
        "const GRAPH = " + json.dumps(graph, separators=(",", ":")) + ";",
    ]
    if clusters_line:
        lines.append('const CLUSTERS = {"clusters":[],"central":[]};')
    lines += ["renderClusters();", "</script></body></html>", ""]
    return "\n".join(lines)


def ring(n, prefix="n", type_="wikipage"):
    """A connected ring of n nodes, large enough to be a named cluster."""
    nodes = [{"id": f"{prefix}{i}", "label": f"{prefix}{i}", "type": type_, "detail": ""}
             for i in range(n)]
    links = [{"source": f"{prefix}{i}", "target": f"{prefix}{(i + 1) % n}"} for i in range(n)]
    return {"nodes": nodes, "links": links}


def clique(n, prefix="n", type_="wikipage"):
    """A fully connected block of n nodes; stays one modularity community."""
    nodes = [{"id": f"{prefix}{i}", "label": f"{prefix}{i}", "type": type_, "detail": ""}
             for i in range(n)]
    links = [{"source": f"{prefix}{i}", "target": f"{prefix}{j}"}
             for i in range(n) for j in range(i + 1, n)]
    return {"nodes": nodes, "links": links}


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class LoadGraphTests(unittest.TestCase):
    def test_reads_the_embedded_graph_literal(self):
        graph = ring(3)
        self.assertEqual(gc.load_graph(page(graph)), graph)

    def test_exits_when_the_graph_line_is_absent(self):
        with self.assertRaises(SystemExit):
            gc.load_graph("<html><script>const OTHER = 1;</script></html>")


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class BuildTests(unittest.TestCase):
    def test_links_to_unknown_nodes_are_dropped(self):
        graph = ring(3)
        graph["links"].append({"source": "n0", "target": "ghost"})
        nodes, g = gc.build(graph)
        self.assertEqual(set(nodes), {"n0", "n1", "n2"})
        self.assertNotIn("ghost", g)
        self.assertEqual(g.number_of_edges(), 3)


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class SummarizeTests(unittest.TestCase):
    def test_reports_every_component_but_analyses_the_largest(self):
        graph = ring(9)
        graph["nodes"] += [{"id": "iso", "label": "iso", "type": "agent", "detail": ""}]
        nodes, g = gc.build(graph)
        summary = gc.summarize(nodes, g)
        self.assertEqual(summary["components"], [9, 1])
        members = [m for c in summary["clusters"] for m in c["members"]]
        self.assertNotIn("iso", members)

    def test_clusters_below_the_threshold_fold_into_smaller_clusters(self):
        # Two rings joined by a single edge: the small side is under MIN_NAMED.
        graph = ring(12, prefix="big")
        small = ring(3, prefix="small")
        graph["nodes"] += small["nodes"]
        graph["links"] += small["links"]
        graph["links"].append({"source": "big0", "target": "small0"})
        nodes, g = gc.build(graph)
        summary = gc.summarize(nodes, g)
        named = [c for c in summary["clusters"] if c["name"] != "smaller clusters"]
        for cluster in named:
            self.assertGreaterEqual(cluster["size"], gc.MIN_NAMED)


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class NameClusterTests(unittest.TestCase):
    """Naming is deterministic, unlike the community split it feeds on."""

    def setUp(self):
        self.nodes = {
            "n0": {"id": "n0", "label": "vanderbi.lt", "type": "shortener"},
            "n1": {"id": "n1", "label": "AgentZed", "type": "agent"},
            "n2": {"id": "n2", "label": "jqp", "type": "proxy"},
            "n3": {"id": "n3", "label": "StartSeite", "type": "wikipage"},
        }

    def test_names_after_the_two_highest_degree_non_agent_members(self):
        degree = {"n0": 50, "n1": 40, "n2": 30, "n3": 10}
        name = gc.name_cluster(self.nodes, set(self.nodes), degree)
        self.assertEqual(name, "vanderbi.lt + jqp")

    def test_agents_are_skipped_even_when_they_are_the_top_hub(self):
        degree = {"n0": 10, "n1": 99, "n2": 5, "n3": 7}
        name = gc.name_cluster(self.nodes, set(self.nodes), degree)
        self.assertNotIn("AgentZed", name)
        self.assertEqual(name, "vanderbi.lt + StartSeite")

    def test_falls_back_to_the_top_member_when_every_member_is_an_agent(self):
        nodes = {k: dict(v, type="agent") for k, v in self.nodes.items()}
        degree = {"n0": 1, "n1": 99, "n2": 2, "n3": 3}
        self.assertEqual(gc.name_cluster(nodes, set(nodes), degree), "AgentZed")


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class RewriteTests(unittest.TestCase):
    def test_replaces_the_clusters_line_and_leaves_graph_untouched(self):
        graph = ring(3)
        text = page(graph)
        out = gc.rewrite(text, {"clusters": [], "central": [], "communities": 2})
        self.assertEqual(gc.load_graph(out), graph)
        self.assertEqual(out.count("const CLUSTERS = "), 1)
        self.assertIn('"communities":2', out)

    def test_inserts_a_clusters_line_when_the_page_has_none(self):
        text = page(ring(3), clusters_line=False)
        self.assertNotIn("const CLUSTERS", text)
        out = gc.rewrite(text, {"clusters": [], "central": []})
        self.assertEqual(out.count("const CLUSTERS = "), 1)
        graph_at = out.index("const GRAPH = ")
        self.assertGreater(out.index("const CLUSTERS = "), graph_at)

    def test_backslashes_in_a_cluster_name_survive_the_substitution(self):
        # re.sub would read \1 / \g<0> in a replacement string as a backreference.
        summary = {"clusters": [{"name": r"page:C:\temp \1 \g<0>", "size": 9}], "central": []}
        out = gc.rewrite(page(ring(3)), summary)
        line = next(l for l in out.splitlines() if l.startswith("const CLUSTERS = "))
        parsed = json.loads(line[len("const CLUSTERS = "):-1])
        self.assertEqual(parsed["clusters"][0]["name"], r"page:C:\temp \1 \g<0>")


@unittest.skipIf(gc is None, f"needs {_MISSING}")
class MainTests(unittest.TestCase):
    def test_rewrites_the_page_in_place_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.html"
            graph = ring(10)
            target.write_text(page(graph))
            gc.main(target)
            first = target.read_text()
            gc.main(target)
            self.assertEqual(target.read_text(), first)
            self.assertEqual(gc.load_graph(first), graph)
            line = next(l for l in first.splitlines() if l.startswith("const CLUSTERS = "))
            summary = json.loads(line[len("const CLUSTERS = "):-1])
            # main() writes one summary per view. A page with no RELAY line
            # carries the provenance axis alone; this ring has no termina-db
            # layer, so both views see the same graph.
            self.assertEqual(list(summary), ["atlas", "operational"])
            for mode in ("atlas", "operational"):
                self.assertEqual(summary[mode]["components"], [10])
                self.assertTrue(summary[mode]["clusters"])


if __name__ == "__main__":
    unittest.main()
