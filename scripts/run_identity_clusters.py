#!/usr/bin/env python3
"""Validate task-family clustering in the reply graphs on run-identity.html.

The graph is clustered without using the audited ``family`` labels.  Those
labels are revealed only afterward to score community purity, adjusted Rand
agreement, and normalized mutual information.  A label-permutation null tests
the weighted within-family reply share, and edge-drop trials show how quickly
the run partition degrades when observed reply pairs are removed.

Running this script writes ``data/run_identity_clusters.json`` and refreshes
the compact ``CLUSTER_ANALYSIS`` object embedded in the page.
"""
import argparse
import collections
import json
import math
import random
import re
from pathlib import Path

import networkx as nx
from networkx.algorithms import community

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "run-identity.html"
OUTPUT = ROOT / "data" / "run_identity_clusters.json"
GRAPH_RE = re.compile(r"^const D = (.*);\s*$", re.M)
RESULT_RE = re.compile(r"^const CLUSTER_ANALYSIS = .*;\s*$", re.M)
SEED = 123


def choose2(value):
    return value * (value - 1) / 2


def adjusted_rand(labels, clusters):
    table = collections.Counter(zip(labels, clusters))
    label_sizes = collections.Counter(labels)
    cluster_sizes = collections.Counter(clusters)
    pairs = choose2(len(labels))
    overlap = sum(choose2(n) for n in table.values())
    label_pairs = sum(choose2(n) for n in label_sizes.values())
    cluster_pairs = sum(choose2(n) for n in cluster_sizes.values())
    expected = label_pairs * cluster_pairs / pairs
    denominator = (label_pairs + cluster_pairs) / 2 - expected
    return (overlap - expected) / denominator if denominator else 1.0


def normalized_mutual_information(labels, clusters):
    table = collections.Counter(zip(labels, clusters))
    label_sizes = collections.Counter(labels)
    cluster_sizes = collections.Counter(clusters)
    total = len(labels)
    mutual_information = sum(
        count / total * math.log(count * total / (label_sizes[label] * cluster_sizes[cluster]))
        for (label, cluster), count in table.items()
    )
    label_entropy = -sum(count / total * math.log(count / total) for count in label_sizes.values())
    cluster_entropy = -sum(count / total * math.log(count / total) for count in cluster_sizes.values())
    return 2 * mutual_information / (label_entropy + cluster_entropy)


def load_page_graphs(text):
    match = GRAPH_RE.search(text)
    if not match:
        raise SystemExit("run-identity.html: no `const D = ...;` line")
    data = json.loads(match.group(1))
    return {mode: build_graph(data[mode]) for mode in ("label", "run")}


def build_graph(data):
    graph = nx.Graph()
    for node in data["nodes"]:
        graph.add_node(node["id"], family=node.get("family"), name=node.get("name"))
    for edge in data["edges"]:
        graph.add_edge(edge["s"], edge["t"], weight=edge["w"])
    return graph


def partition_labels(graph, communities):
    membership = {node: index for index, members in enumerate(communities) for node in members}
    nodes = list(graph)
    return nodes, [graph.nodes[node]["family"] for node in nodes], [membership[node] for node in nodes]


def summarize(graph):
    communities = sorted(
        community.greedy_modularity_communities(graph, weight="weight"),
        key=lambda members: (-len(members), sorted(members)),
    )
    nodes, families, memberships = partition_labels(graph, communities)
    total_weight = sum(data["weight"] for _, _, data in graph.edges(data=True))
    same_weight = sum(
        data["weight"]
        for left, right, data in graph.edges(data=True)
        if graph.nodes[left]["family"] == graph.nodes[right]["family"]
    )
    purity = sum(
        max(collections.Counter(graph.nodes[node]["family"] for node in members).values())
        for members in communities
    ) / graph.number_of_nodes()
    family_partition = [
        {node for node in graph if graph.nodes[node]["family"] == family}
        for family in sorted(set(families))
    ]
    mixed = []
    family_membership = collections.defaultdict(collections.Counter)
    for index, members in enumerate(communities, 1):
        counts = collections.Counter(graph.nodes[node]["family"] for node in members)
        if len(counts) > 1:
            mixed.append({"community": index, "size": len(members), "families": dict(counts.most_common())})
        for family, count in counts.items():
            family_membership[family][index] += count
    split = [
        {"family": family, "size": sum(counts.values()), "communities": dict(counts)}
        for family, counts in sorted(family_membership.items(), key=lambda item: (-sum(item[1].values()), item[0]))
        if len(counts) > 1
    ]
    return {
        "nodes": graph.number_of_nodes(),
        "reply_pairs": graph.number_of_edges(),
        "weighted_replies": total_weight,
        "components": nx.number_connected_components(graph),
        "families": len(set(families)),
        "communities": len(communities),
        "modularity": round(community.modularity(graph, communities, weight="weight"), 4),
        "family_partition_modularity": round(community.modularity(graph, family_partition, weight="weight"), 4),
        "purity": round(purity, 4),
        "adjusted_rand": round(adjusted_rand(families, memberships), 4),
        "normalized_mutual_information": round(normalized_mutual_information(families, memberships), 4),
        "same_family_weight": same_weight,
        "same_family_share": round(same_weight / total_weight, 4),
        "family_assortativity": round(nx.attribute_assortativity_coefficient(graph, "family"), 4),
        "single_family_communities": sum(
            len({graph.nodes[node]["family"] for node in members}) == 1 for members in communities
        ),
        "largest_communities": [len(members) for members in communities[:8]],
        "mixed_communities": mixed,
        "split_families": split,
    }


def permutation_test(graph, iterations):
    rng = random.Random(SEED)
    nodes = list(graph)
    families = [graph.nodes[node]["family"] for node in nodes]
    total_weight = sum(data["weight"] for _, _, data in graph.edges(data=True))
    observed = sum(
        data["weight"]
        for left, right, data in graph.edges(data=True)
        if graph.nodes[left]["family"] == graph.nodes[right]["family"]
    ) / total_weight
    null = []
    for _ in range(iterations):
        shuffled = families[:]
        rng.shuffle(shuffled)
        labels = dict(zip(nodes, shuffled))
        null.append(
            sum(data["weight"] for left, right, data in graph.edges(data=True) if labels[left] == labels[right])
            / total_weight
        )
    mean = sum(null) / len(null)
    variance = sum((value - mean) ** 2 for value in null) / (len(null) - 1)
    return {
        "iterations": iterations,
        "seed": SEED,
        "observed": round(observed, 4),
        "null_mean": round(mean, 4),
        "null_sd": round(math.sqrt(variance), 4),
        "null_max": round(max(null), 4),
        "p_upper": round((1 + sum(value >= observed for value in null)) / (iterations + 1), 6),
    }


def robustness(graph, trials):
    nodes = list(graph)
    families = [graph.nodes[node]["family"] for node in nodes]
    rows = []
    for drop in (0.0, 0.1, 0.2, 0.3):
        agreements = []
        counts = []
        for trial in range(trials):
            sample = graph.copy()
            if drop:
                rng = random.Random(1000 + trial)
                sample.remove_edges_from([edge for edge in sample.edges if rng.random() < drop])
            communities = community.louvain_communities(sample, weight="weight", seed=trial)
            _, _, memberships = partition_labels(sample, communities)
            agreements.append(adjusted_rand(families, memberships))
            counts.append(len(communities))
        agreements.sort()
        counts.sort()
        rows.append({
            "edge_drop": drop,
            "trials": trials,
            "median_adjusted_rand": round(agreements[len(agreements) // 2], 4),
            "min_adjusted_rand": round(agreements[0], 4),
            "max_adjusted_rand": round(agreements[-1], 4),
            "median_communities": counts[len(counts) // 2],
            "min_communities": counts[0],
            "max_communities": counts[-1],
        })
    return rows


def analyze(graphs, permutations, trials):
    return {
        "method": "weighted greedy modularity; audited family labels withheld until validation",
        "modes": {mode: summarize(graph) for mode, graph in graphs.items()},
        "run_family_permutation": permutation_test(graphs["run"], permutations),
        "run_edge_drop_robustness": robustness(graphs["run"], trials),
    }


def write_results(page_text, results):
    OUTPUT.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    line = "const CLUSTER_ANALYSIS = " + json.dumps(results, separators=(",", ":"), sort_keys=True) + ";"
    if not RESULT_RE.search(page_text):
        raise SystemExit("run-identity.html: no `const CLUSTER_ANALYSIS = ...;` line")
    PAGE.write_text(RESULT_RE.sub(lambda _: line, page_text, count=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--permutations", type=int, default=10_000)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args()
    page_text = PAGE.read_text()
    results = analyze(load_page_graphs(page_text), args.permutations, args.trials)
    if args.check:
        expected_json = json.dumps(results, indent=2, sort_keys=True) + "\n"
        expected_line = "const CLUSTER_ANALYSIS = " + json.dumps(
            results, separators=(",", ":"), sort_keys=True
        ) + ";"
        if not OUTPUT.exists() or OUTPUT.read_text() != expected_json or RESULT_RE.search(page_text).group(0) != expected_line:
            raise SystemExit("run-identity cluster analysis is stale; rerun this script")
    else:
        write_results(page_text, results)
    run = results["modes"]["run"]
    print(
        f"run: {run['communities']} communities, modularity {run['modularity']:.3f}, "
        f"purity {run['purity']:.3f}, ARI {run['adjusted_rand']:.3f}, "
        f"NMI {run['normalized_mutual_information']:.3f}"
    )


if __name__ == "__main__":
    main()
