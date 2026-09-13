"""Compute cluster and centrality summaries for graph.html.

Reads the embedded ``const GRAPH = {...};`` literal from graph.html, runs
connected components, greedy-modularity communities, degree and betweenness
on the undirected view, and rewrites the ``const CLUSTERS = {...};`` line
that the "Clusters" card renders. Computed twice: ``atlas`` for the atlas
alone and ``combined`` with the relay layer (``const RELAY``, written by
scripts/atlas_relay.py) merged in. Run after any atlas augment:

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
TOP_HUBS = 4
TOP_CENTRAL = 10
MIN_NAMED = 8  # clusters smaller than this are folded into "smaller clusters"


def load_graph(text):
    m = GRAPH_RE.search(text)
    if not m:
        sys.exit("graph.html: no `const GRAPH = ...;` line")
    return json.loads(m.group(1))


def build(graph, relay=None):
    nodes = {n["id"]: n for n in graph["nodes"]}
    if relay:
        nodes.update({n["id"]: n for n in relay["nodes"]})
    g = nx.Graph()
    g.add_nodes_from(sorted(nodes))
    for l in graph["links"] + (relay["links"] if relay else []):
        if l["source"] in nodes and l["target"] in nodes:
            g.add_edge(l["source"], l["target"])
    return nodes, g


def by_degree(ids, degree):
    """Highest degree first, ties broken by id.

    networkx hands back sets of string ids, whose iteration order changes with
    PYTHONHASHSEED; every ordering here needs a total key or the CLUSTERS line
    differs between runs.
    """
    return sorted(ids, key=lambda i: (-degree[i], i))


def by_size(groups):
    """Largest first, ties broken by smallest member id."""
    return sorted(groups, key=lambda c: (-len(c), min(c)))


def top_types(nodes, members, k=3):
    """The k commonest node types, count descending then type name."""
    counts = collections.Counter(nodes[i]["type"] for i in members)
    return dict(sorted(counts.items(), key=lambda t: (-t[1], t[0]))[:k])


def name_cluster(nodes, members, degree):
    """Name a cluster after its two highest-degree non-agent members."""
    ranked = by_degree(members, degree)
    hubs = [i for i in ranked if nodes[i]["type"] not in ("agent", "run")][:2]
    return " + ".join(nodes[i]["label"] for i in hubs) or nodes[ranked[0]]["label"]


def summarize(nodes, g):
    comps = by_size(nx.connected_components(g))
    big = g.subgraph(comps[0])
    bc = nx.betweenness_centrality(big)
    deg = dict(big.degree())
    comms = by_size(community.greedy_modularity_communities(big))
    clusters = []
    rest = []
    for c in comms:
        if len(c) < MIN_NAMED:
            rest.extend(c)
            continue
        hubs = by_degree(c, deg)[:TOP_HUBS]
        clusters.append({
            "name": name_cluster(nodes, c, deg),
            "size": len(c),
            "types": top_types(nodes, c),
            "hubs": [{"id": i, "deg": deg[i], "bc": round(bc[i], 3)} for i in hubs],
            "members": sorted(c),
        })
    if rest:
        clusters.append({"name": "smaller clusters", "size": len(rest), "types": {}, "hubs": [], "members": sorted(rest)})
    central = [{"id": i, "deg": deg[i], "bc": round(v, 3)} for i, v in sorted(bc.items(), key=lambda x: (-x[1], x[0]))[:TOP_CENTRAL]]
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


def main(page=PAGE):
    text = page.read_text()
    graph = load_graph(text)
    rm = RELAY_RE.search(text)
    relay = json.loads(rm.group(1)) if rm else None
    summary = {"atlas": summarize(*build(graph))}
    if relay:
        summary["combined"] = summarize(*build(graph, relay))
    page.write_text(rewrite(text, summary))
    for mode, s in summary.items():
        print(f"== {mode}")
        for c in s["clusters"]:
            print(f"{c['size']:4d}  {c['name']}")
        print(f"components {s['components']}, communities {s['communities']}, articulation points {s['articulation']}")


if __name__ == "__main__":
    main()
