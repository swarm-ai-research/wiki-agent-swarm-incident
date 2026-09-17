"""Compute cluster and centrality summaries for graph.html.

Reads the embedded ``const GRAPH = {...};`` literal from graph.html, runs
connected components, greedy-modularity communities, degree and betweenness
on the undirected view, and rewrites the ``const CLUSTERS = {...};`` line
that the "Clusters" card renders.

Two independent axes select a view. The *relay* axis merges the relay layer
(``const RELAY``, written by scripts/atlas_relay.py) into the atlas; the
*provenance* axis drops the termina summary layer -- edges carrying
``src: termina``, plus ``incident``, ``campaign`` and ``cluster`` nodes when
that layer is embedded as nodes -- leaving only surfaces the archive observed
directly. They compose, so up to
four summaries are written: ``atlas`` and ``operational`` without the relay
layer, ``combined`` and ``combined_operational`` with it. Run after any atlas
augment:

    python3 scripts/graph_clusters.py
"""
import collections
import json
import re
import sys
from pathlib import Path

import networkx as nx
from networkx.algorithms import community

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "graph.html"
GRAPH_RE = re.compile(r"^const GRAPH = (.*);\s*$", re.M)
CLUSTERS_RE = re.compile(r"^const CLUSTERS = .*;\s*$", re.M)
RELAY_RE = re.compile(r"^const RELAY = (.*);\s*$", re.M)
PROVENANCE_TYPES = {"incident", "campaign", "cluster"}
TOP_HUBS = 4
TOP_CENTRAL = 10
MIN_NAMED = 8  # clusters smaller than this are folded into "smaller clusters"


def is_provenance_node(node):
    """A node from the termina-db summary layer rather than an observed surface."""
    return node.get("type") in PROVENANCE_TYPES


def is_provenance_link(link):
    # The atlas stamps termina-derived edges ``src: termina``; the summary-node
    # types above are kept for when that layer is embedded as nodes again.
    return link.get("src") == "termina"


def load_graph(text):
    m = GRAPH_RE.search(text)
    if not m:
        sys.exit("graph.html: no `const GRAPH = ...;` line")
    return json.loads(m.group(1))


def build(graph, relay=None, include_provenance=True):
    """Nodes and undirected graph for one view.

    ``relay`` merges the relay layer in; ``include_provenance=False`` drops the
    termina-db summary layer. The two are independent, so any combination is a
    valid view.
    """
    keep = (lambda n: True) if include_provenance else (lambda n: not is_provenance_node(n))
    nodes = {n["id"]: n for n in graph["nodes"] if keep(n)}
    if relay:
        nodes.update({n["id"]: n for n in relay["nodes"] if keep(n)})
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for l in graph["links"] + (relay["links"] if relay else []):
        if not include_provenance and is_provenance_link(l):
            continue
        if l["source"] in nodes and l["target"] in nodes:
            g.add_edge(l["source"], l["target"])
    return nodes, g


def name_cluster(nodes, members, degree):
    """Name a cluster after its two highest-degree non-agent members."""
    hubs = [i for i in sorted(members, key=lambda i: -degree[i]) if nodes[i]["type"] not in ("agent", "run")][:2]
    return " + ".join(nodes[i]["label"] for i in hubs) or nodes[max(members, key=lambda i: degree[i])]["label"]


def summarize(nodes, g):
    comps = sorted(nx.connected_components(g), key=len, reverse=True)
    big = g.subgraph(comps[0])
    bc = nx.betweenness_centrality(big)
    deg = dict(big.degree())
    comms = sorted(community.greedy_modularity_communities(big), key=len, reverse=True)
    clusters = []
    rest = []
    for c in comms:
        if len(c) < MIN_NAMED:
            rest.extend(c)
            continue
        hubs = sorted(c, key=lambda i: -deg[i])[:TOP_HUBS]
        clusters.append({
            "name": name_cluster(nodes, c, deg),
            "size": len(c),
            "types": dict(collections.Counter(nodes[i]["type"] for i in c).most_common(3)),
            "hubs": [{"id": i, "deg": deg[i], "bc": round(bc[i], 3)} for i in hubs],
            "members": sorted(c),
        })
    if rest:
        clusters.append({"name": "smaller clusters", "size": len(rest), "types": {}, "hubs": [], "members": sorted(rest)})
    central = [{"id": i, "deg": deg[i], "bc": round(v, 3)} for i, v in sorted(bc.items(), key=lambda x: -x[1])[:TOP_CENTRAL]]
    return {
        "clusters": clusters,
        "central": central,
        "communities": len(comms),
        "articulation": len(list(nx.articulation_points(big))),
        "components": [len(c) for c in comps],
    }


def rewrite(text, summary):
    """Replace the CLUSTERS line, or insert one right after GRAPH if absent."""
    line = "const CLUSTERS = " + json.dumps(summary, separators=(",", ":")) + ";"
    if CLUSTERS_RE.search(text):
        return CLUSTERS_RE.sub(lambda _: line, text, count=1)
    return GRAPH_RE.sub(lambda m: m.group(0) + "\n" + line, text, count=1)


def summarize_views(graph, relay=None):
    """One summary per view: the provenance axis always, the relay axis if present."""
    views = {
        "atlas": build(graph),
        "operational": build(graph, include_provenance=False),
    }
    if relay:
        views["combined"] = build(graph, relay)
        views["combined_operational"] = build(graph, relay, include_provenance=False)
    return {mode: summarize(*v) for mode, v in views.items()}


def main(page=PAGE):
    text = page.read_text()
    graph = load_graph(text)
    rm = RELAY_RE.search(text)
    relay = json.loads(rm.group(1)) if rm else None
    summary = summarize_views(graph, relay)
    page.write_text(rewrite(text, summary))
    for mode, s in summary.items():
        print(f"== {mode}")
        for c in s["clusters"]:
            print(f"{c['size']:4d}  {c['name']}")
        print(f"components {s['components']}, communities {s['communities']}, articulation points {s['articulation']}")


if __name__ == "__main__":
    main()
