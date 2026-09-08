"""Embed the run-identity relay layer into graph.html.

Reads data/run_identity_map.json (the fast-follow-question-trajectories
reconstruction: export revisions owned by audited runs, each run on one task)
and rewrites the ``const RELAY = {...};`` line in graph.html. The layer adds
run and task nodes plus the pages those runs own, and joins the atlas through
DSEWiki (hosts), pages both datasets share, and handles whose name is a run
name. graph.html draws it only when the relay toggle is on. Run after
scripts/atlas_augment.py and before scripts/graph_clusters.py:

    python3 scripts/atlas_relay.py && python3 scripts/graph_clusters.py
"""
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "graph.html"
MAP = ROOT / "data" / "run_identity_map.json"
GRAPH_RE = re.compile(r"^const GRAPH = (.*);\s*$", re.M)
RELAY_RE = re.compile(r"^const RELAY = .*;\s*$", re.M)
WIKI_LABEL = "DSEWiki"
SRC = "run-identity"


def family(task_id):
    return task_id.split("-")[0]


def build(graph, m):
    atlas = {n["id"]: n for n in graph["nodes"]}
    wiki_id = next((n["id"] for n in graph["nodes"] if n["type"] == "wiki" and n["label"] == WIKI_LABEL), None)
    by_name = {n["id"].split(":", 1)[1]: n["id"] for n in graph["nodes"] if n["type"] in ("agent", "wikipage")}
    runs = m["runs"]
    owned = collections.Counter()  # (run, page name) -> revisions
    for key, v in m["revisions"].items():
        name = key.split("~", 1)[1].rsplit("@", 1)[0]
        owned[(v["run"], name)] += 1
    nodes, links, fams = [], [], {}
    tasks = collections.Counter(runs[r]["task_id"] for r, _ in owned)
    for t, n in sorted(tasks.items()):
        nodes.append({"id": "task:" + t, "label": t, "type": "task", "layer": "relay", "fam": family(t),
                      "detail": f"Task family {family(t)}: {n} run-page pairs in the reconstruction.", "src": SRC})
    pages = collections.defaultdict(set)
    for (r, name), n in owned.items():
        pages[name].add(runs[r]["task_id"])
    for name, ts in sorted(pages.items()):
        fam = collections.Counter(family(t) for t in ts).most_common(1)[0][0]
        if name in by_name:
            fams[by_name[name]] = fam
            continue
        pid = "page:" + name
        nodes.append({"id": pid, "label": name, "type": "wikipage", "layer": "relay", "fam": fam,
                      "detail": f"Page owned by audited runs on {', '.join(sorted(ts))}.", "src": SRC})
        if wiki_id:
            links.append({"source": wiki_id, "target": pid, "rt": "hosts", "rel": "hosts", "src": SRC, "layer": "relay"})
    seen_runs = set()
    for (r, name), n in sorted(owned.items()):
        info = runs[r]
        if r not in seen_runs:
            seen_runs.add(r)
            rid = "run:" + r
            nodes.append({"id": rid, "label": info["name"], "type": "run", "layer": "relay", "fam": family(info["task_id"]),
                          "detail": f"Run {r} on {info['task_id']} ({'supported' if info.get('supported') else 'provisional'}; {info.get('status', '')}).",
                          "src": SRC})
            links.append({"source": rid, "target": "task:" + info["task_id"], "rt": "works_on", "rel": "works on", "src": SRC, "layer": "relay"})
            if info["name"] in by_name and atlas[by_name[info["name"]]]["type"] == "agent":
                fams[by_name[info["name"]]] = family(info["task_id"])
                links.append({"source": rid, "target": by_name[info["name"]], "rt": "same_name", "rel": "same name as atlas handle", "src": SRC, "layer": "relay"})
        pid = by_name.get(name, "page:" + name)
        links.append({"source": "run:" + r, "target": pid, "rt": "edits", "rel": f"edits ({n} rev{'s' if n != 1 else ''})", "src": SRC, "layer": "relay", "n": n})
    return {"note": "Relay layer from data/run_identity_map.json; applied by scripts/atlas_relay.py.",
            "source": m.get("source"), "source_commit": m.get("source_commit"),
            "nodes": nodes, "links": links, "fams": fams}


def main():
    text = PAGE.read_text()
    gm = GRAPH_RE.search(text)
    if not gm:
        sys.exit("graph.html: no `const GRAPH = ...;` line")
    relay = build(json.loads(gm.group(1)), json.loads(MAP.read_text()))
    line = "const RELAY = " + json.dumps(relay, separators=(",", ":")) + ";"
    if RELAY_RE.search(text):
        text = RELAY_RE.sub(lambda _: line, text, count=1)
    else:
        text = GRAPH_RE.sub(lambda mm: mm.group(0) + "\n" + line, text, count=1)
    PAGE.write_text(text)
    c = collections.Counter(n["type"] for n in relay["nodes"])
    print(f"relay layer: {len(relay['nodes'])} nodes {dict(c)}, {len(relay['links'])} links, {len(relay['fams'])} shared atlas nodes given a family")
    print("families:", dict(collections.Counter(n["fam"] for n in relay["nodes"] if n["type"] == "task")))


if __name__ == "__main__":
    main()
