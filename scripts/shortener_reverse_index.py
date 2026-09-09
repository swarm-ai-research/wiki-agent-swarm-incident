#!/usr/bin/env python3
"""Build destination-keyed reverse indexes for known swarm short URLs.

This is an offline transform. It reads the rendered Atlas graph and the archived
short-code disposition ledger; it never requests a short URL.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graph.html"
LEDGER = ROOT / "data" / "shortener_code_resolution_ledger_2026-09-08.json"
JSON_OUTPUT = ROOT / "data" / "shortener_reverse_index_2026-09-09.json"
CSV_OUTPUT = ROOT / "data" / "shortener_reverse_index_2026-09-09.csv"
GRAPH_MARKER = "const GRAPH = "
FLOW_RELATIONS = {"resolves_to", "proxies", "pings"}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_graph(path: Path = GRAPH) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.find(GRAPH_MARKER)
    if start < 0:
        raise ValueError(f"{GRAPH_MARKER!r} not found in {path}")
    start += len(GRAPH_MARKER)
    graph, _end = json.JSONDecoder().raw_decode(text[start:])
    return graph


def code_from_id(node_id: str) -> str:
    return node_id.removeprefix("short:")


def normalize_code(code: str) -> str:
    """Normalise a short code for matching.

    Only the host is case-insensitive. The path after the first "/" is not:
    ``is.gd/AbC`` and ``is.gd/abc`` are different links, and folding them
    together would silently attach one code's archive disposition to another.
    """
    host, separator, path = code.partition("/")
    return host.casefold() + separator + path


def index_ledger(ledger: dict) -> dict[str, dict]:
    """Key ledger rows by normalised code, refusing to merge distinct codes.

    Within ``codes`` a later row wins, and ``discovered_hops`` only fill gaps —
    the original precedence. What is new is that two *different* raw codes
    mapping to one key is an error rather than a silent drop.
    """
    by_code: dict[str, dict] = {}
    raw_for_key: dict[str, str] = {}

    def add(row: dict, *, overwrite: bool) -> None:
        key = normalize_code(row["code"])
        prior = raw_for_key.get(key)
        if prior is not None and prior != row["code"]:
            raise ValueError(
                f"short-code collision: {prior!r} and {row['code']!r} both "
                f"normalise to {key!r}; short-code paths are case-sensitive "
                "and must not be folded together"
            )
        raw_for_key[key] = row["code"]
        if overwrite or key not in by_code:
            by_code[key] = row

    for row in ledger["codes"]:
        add(row, overwrite=True)
    for row in ledger.get("discovered_hops", []):
        add(row, overwrite=False)
    return by_code


def _paths(graph: dict, start: str, max_depth: int = 6) -> list[tuple[str, list[str]]]:
    """Return flow-reachable nodes and paths, avoiding cycles.

    All proxy-derived results remain candidates: shared proxy nodes intentionally
    collapse several concrete target URLs in the Atlas.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in graph["links"]:
        if edge.get("rt") in FLOW_RELATIONS:
            adjacency[edge["source"]].append(edge["target"])
    found: dict[str, list[str]] = {}
    queue = deque([(start, [start])])
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for target in sorted(set(adjacency.get(node, []))):
            if target in path:
                continue
            new_path = path + [target]
            if target != start and target not in found:
                found[target] = new_path
            queue.append((target, new_path))
    return sorted(found.items())


def build(graph_path: Path = GRAPH, ledger_path: Path = LEDGER) -> dict:
    graph = load_graph(graph_path)
    nodes = {node["id"]: node for node in graph["nodes"]}
    short_ids = sorted(node_id for node_id, node in nodes.items() if node.get("type") == "shortener")
    direct = defaultdict(list)
    for edge in graph["links"]:
        if edge.get("rt") == "resolves_to" and edge["source"] in short_ids:
            direct[edge["source"]].append(edge["target"])

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_by_code = index_ledger(ledger)

    destinations: dict[str, dict] = {}
    codes = []
    resolved_graph_codes = set()
    for short_id in short_ids:
        code = code_from_id(short_id)
        immediate = sorted(set(direct.get(short_id, [])))
        if immediate:
            resolved_graph_codes.add(normalize_code(code))
        paths = _paths(graph, short_id)
        relations = []
        for target, path in paths:
            if len(path) == 2:
                relation, confidence = "direct", "observed"
            elif all(nodes.get(item, {}).get("type") == "shortener" for item in path[1:-1]):
                relation, confidence = "nested", "observed-chain"
            else:
                relation, confidence = "reachable", "topology-candidate"
            rel = {"target_id": target, "relation": relation, "confidence": confidence, "path": path}
            relations.append(rel)
            target_node = nodes.get(target, {"id": target, "label": target, "type": "unknown"})
            entry = destinations.setdefault(target, {
                "target_id": target,
                "label": target_node.get("label", target),
                "type": target_node.get("type", "unknown"),
                "matches": [],
            })
            entry["matches"].append({"short_code": code, **{k: rel[k] for k in ("relation", "confidence", "path")}})
        ledger_row = ledger_by_code.get(normalize_code(code))
        codes.append({
            "short_code": code,
            "host": code.split("/", 1)[0].casefold(),
            "immediate_targets": immediate,
            "relations": relations,
            "graph_status": "resolved" if immediate else "unresolved",
            "archive_disposition": ledger_row.get("disposition") if ledger_row else None,
            "archive_resolution": ledger_row.get("resolution") if ledger_row else None,
        })

    graph_codes = {normalize_code(row["short_code"]) for row in codes}
    for key, row in sorted(ledger_by_code.items()):
        if key in graph_codes:
            continue
        code = row["code"]
        codes.append({
            "short_code": code,
            "host": code.split("/", 1)[0].casefold(),
            "immediate_targets": [],
            "relations": [],
            "graph_status": "not-in-graph",
            "archive_disposition": row.get("disposition"),
            "archive_resolution": row.get("resolution"),
        })

    confidence_for_relation = {
        "direct": "observed",
        "nested": "observed-chain",
        "reachable": "topology-candidate",
    }
    relation_rank = {"direct": 0, "nested": 1, "reachable": 2}
    for entry in destinations.values():
        entry["matches"].sort(key=lambda row: (relation_rank[row["relation"]], row["short_code"].casefold()))
        entry["counts"] = {
            relation: sum(row["relation"] == relation for row in entry["matches"])
            for relation in ("direct", "nested", "reachable")
        }
    relation_totals = Counter(
        match["relation"]
        for entry in destinations.values()
        for match in entry["matches"]
    )
    codes.sort(key=lambda row: row["short_code"].casefold())
    unresolved = [
        row["short_code"] for row in codes
        if not row["immediate_targets"] and row["archive_disposition"] != "archived-target-recovered"
    ]
    return {
        "generated_on": date.today().isoformat(),
        "network_policy": "offline-only; no live shortener or Archive request",
        "inputs": {"graph": display_path(graph_path), "archive_ledger": display_path(ledger_path)},
        "method": {
            "direct": "Atlas resolves_to edge from a shortener",
            "nested": "Atlas chain containing only shortener nodes between source and target",
            "reachable": "candidate reachability through shared proxy topology; not an exact code-to-final-target assertion",
            "counts.graph_codes_with_direct_resolution": "number of short codes carrying at least one Atlas resolves_to edge (codes, not edges)",
            "counts.relations_by_confidence": "how many of the relation rows are observed vs candidate-only; read this before quoting the destination count",
        },
        "counts": {
            "short_codes": len(codes),
            "graph_shortener_nodes": len(short_ids),
            "graph_codes_with_direct_resolution": len(resolved_graph_codes),
            "destinations": len(destinations),
            "unresolved_codes": len(unresolved),
            "relations_total": sum(relation_totals.values()),
            "relations_by_confidence": {
                confidence_for_relation[relation]: relation_totals[relation]
                for relation in ("direct", "nested", "reachable")
            },
        },
        "unresolved_codes": unresolved,
        "destinations": sorted(destinations.values(), key=lambda row: row["target_id"].casefold()),
        "codes": codes,
    }


def csv_text(report: dict) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=("target_id", "target_label", "target_type", "short_code", "relation", "confidence", "path"),
        lineterminator="\n",
    )
    writer.writeheader()
    for target in report["destinations"]:
        for match in target["matches"]:
            writer.writerow({
                "target_id": target["target_id"],
                "target_label": target["label"],
                "target_type": target["type"],
                "short_code": match["short_code"],
                "relation": match["relation"],
                "confidence": match["confidence"],
                "path": " -> ".join(match["path"]),
            })
    return stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=GRAPH)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--json", type=Path, default=JSON_OUTPUT)
    parser.add_argument("--csv", type=Path, default=CSV_OUTPUT)
    args = parser.parse_args()
    report = build(args.graph, args.ledger)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.csv.write_text(csv_text(report), encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
