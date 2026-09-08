#!/usr/bin/env python3
"""Add hand-curated Wayback and third-party-log surfaces to the Backchannel Atlas.

Reads ``data/atlas_wayback_<date>.json`` (nodes and links with a ``src`` of
``wayback`` or ``degraff`` and a ``rel`` string carrying the analysis note's
evidence tag) and merges it into the ``const GRAPH`` literal in ``graph.html``
using the same helpers as ``atlas_augment.py``.  Existing nodes keep their
label, type and detail; a node already present only gains ``first``/``last``
when it had none.  Links are skipped when the pair is already connected, unless the link carries
``annotate: true``, in which case its ``rel`` is appended to the existing edge.

Usage:
    python3 scripts/atlas_wayback.py --data data/atlas_wayback_2026-09-08.json [--graph graph.html] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_augment as aa  # noqa: E402


def apply(graph: dict, data: dict) -> dict:
    nodes, pairs = aa._index(graph)
    added_nodes = added_links = skipped = annotated = 0
    for n in data["nodes"]:
        if n["id"] in nodes:
            cur = nodes[n["id"]]
            for k in ("first", "last"):
                if k in n and k not in cur:
                    cur[k] = n[k]
            continue
        node = aa.add_node(graph, nodes, n["id"], n["type"], n["label"], n.get("detail", ""), n.get("src", ""))
        for k in ("first", "last"):
            if k in n:
                node[k] = n[k]
        added_nodes += 1
    for l in data["links"]:
        for end in ("source", "target"):
            if l[end] not in nodes:
                raise SystemExit(f"unknown node {l[end]!r} in link {l}")
        ok = aa.add_link(graph, pairs, l["source"], l["target"], l["rt"], l["rel"], l["src"], evidence=l.get("evidence"))
        added_links += ok
        if not ok:
            skipped += 1
            if l.get("annotate"):
                annotated += _annotate(graph, l)
    return {"added_nodes": added_nodes, "added_links": added_links, "skipped_links": skipped,
            "annotated_links": annotated, "nodes": len(graph["nodes"]), "links": len(graph["links"])}


def _annotate(graph: dict, l: dict) -> int:
    """Append this note to the rel of the existing edge on the same pair (idempotent)."""
    ends = {l["source"], l["target"]}
    for cur in graph["links"]:
        if {cur["source"], cur["target"]} == ends:
            if l["rel"] in cur["rel"]:
                return 0
            cur["rel"] = f'{cur["rel"]} \u00b7 {l["rel"]}'
            if l.get("evidence") and not cur.get("evidence"):
                cur["evidence"] = l["evidence"]
            return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--graph", type=Path, default=Path("graph.html"))
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    html = a.graph.read_text(encoding="utf-8")
    graph, st, end = aa.load_graph(html)
    report = apply(graph, json.loads(a.data.read_text(encoding="utf-8")))
    print(json.dumps(report))
    if not a.dry_run:
        a.graph.write_text(aa.dump_graph(html, graph, st, end), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
