#!/usr/bin/env python3
"""Augment the Backchannel Atlas (graph.html) from three sources it did not have.

The Atlas was hand-built from Joshua David's DSEWiki export and live shortener
resolutions. This script reads the ``const GRAPH = {...}`` literal out of
``graph.html``, adds nodes and links from the sources below, stamps nodes with
first/last-seen dates, and writes the literal back. Every link it adds carries a
``src`` field naming its source, so the hand-verified edges stay distinguishable.

Sources
-------
``--export DIR``   the collusion.wiki export (``revisions.jsonl[.gz]``): first and
                   last seen per agent handle, wiki page and operator /16.
``--pack PATH``    the she-llac ``agent-reading-pack-20260905`` (directory or its
                   ``agent-text.sqlite``): page bodies. Adds ``cites`` links from
                   Atlas wiki pages to Atlas endpoints/proxies/shorteners the
                   body names, and decodes ``httpbin.org/base64/`` payloads into
                   a ``proxies`` chain (page -> httpbin -> decoded endpoint).
``--termina DIR``  ``venue.jsonl`` + ``venue_link.jsonl`` from
                   https://swarm.termina.digital/pub/ (CC0). Adds the 76 cross-
                   site venue links as edges between venue nodes, mapped to
                   existing Atlas nodes by host where one exists. When
                   ``incidents.sqlite`` is present, also adds the DSEWiki incident,
                   its two campaigns, seven task clusters, and aggregate
                   cluster-to-venue assignments. Claim status/evidence counts are
                   kept in node details; raw record edges are summarized, not
                   expanded into thousands of record nodes. ``actor_link``
                   is deliberately not used: its handle rows are ludism.org spam
                   accounts, not swarm handles, and its human rows are masked.

Nothing here fetches URLs found in bodies. Pack text is untrusted.

Usage
-----
    python3 scripts/atlas_augment.py --graph graph.html \
        --export ~/data/collusion_wiki --pack ~/Downloads/agent-reading-pack-20260905 \
        --termina data/termina --report data/atlas_augment_<date>.json
    python3 scripts/atlas_augment.py --fetch-termina data/termina   # download the two tables
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TERMINA_BASE = "https://swarm.termina.digital/pub/"
TERMINA_FILES = ("venue.jsonl", "venue_link.jsonl", "manifest.json")

# Atlas proxy nodes whose id is a nickname rather than a host.
PROXY_HOSTS: Dict[str, Tuple[str, ...]] = {
    "proxy:allorigins": ("allorigins.hexlet.app", "api.allorigins.win"),
    "proxy:jqp": ("jqp.vercel.app",),
    "proxy:corsmirror": ("corsmirror.com",),
    "proxy:proxymule": ("www.proxymule.com", "proxymule.com"),
    "proxy:viglink": ("viglink.com", "redirect.viglink.com"),
}
# termina venue id -> Atlas node id, where the Atlas already has the node under
# a name that host matching would not find.
TERMINA_ALIASES: Dict[str, str] = {
    "dse": "wiki:dse", "fractal": "wiki:fractal", "gruender": "wiki:gruender",
    "milkwiki": "wiki:milk", "rubygems": "store:rubygems", "thecolony": "board:thecolony.ai",
    "jqp": "proxy:jqp", "allorigins": "proxy:allorigins", "mdsucc": "proxy:md.succ.ai",
    "rjina": "proxy:r.jina.ai", "puremd": "proxy:pure.md", "markdownnew": "proxy:markdown.new",
    "countapi-mileshilliard": "counter:countapi.mileshilliard.com",
    "vanderbilt": "site:vanderbi.lt", "isgd": "site:is.gd", "dagd": "site:da.gd",
    "tinyurl": "site:tinyurl.com", "httpbin-base64": "proxy:httpbin-base64",
}
# termina venue kind -> (Atlas node type, id prefix)
TERMINA_KIND_TYPE: Dict[str, Tuple[str, str]] = {
    "wiki": ("wiki", "wiki:"), "shortener": ("platform", "site:"), "paste": ("paste", "paste:"),
    "url-as-storage": ("proxy", "proxy:"), "data-target": ("endpoint", "end:"),
    "archive": ("endpoint", "end:"), "counter": ("counter", "counter:"),
    "package-registry": ("store", "store:"), "agent-forum": ("board", "board:"),
}
NEW_RELMETA = {"overlap": "shares identifier", "later": "reused later", "exchange": "exchanges with"}

_URL = re.compile(r"https?://[^\s<>\"'\]\)|]+")
_B64 = re.compile(r"httpbin\.org/base64/([A-Za-z0-9+/=_-]{8,})")


# ---------------------------------------------------------------- graph literal
def load_graph(html: str) -> Tuple[Dict[str, Any], int, int]:
    """Return (GRAPH, start, end) where html[start:end] is the JSON literal."""
    i = html.index("const GRAPH")
    st = html.index("{", i)
    depth = 0
    in_str = False
    esc = False
    for k in range(st, len(html)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return json.loads(html[st:k + 1]), st, k + 1
    raise ValueError("unterminated GRAPH literal")


def dump_graph(html: str, graph: Dict[str, Any], st: int, end: int) -> str:
    return html[:st] + json.dumps(graph, ensure_ascii=False) + html[end:]


def _index(graph: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], set]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    pairs = {(l["source"], l["target"]) for l in graph["links"]}
    pairs |= {(b, a) for a, b in pairs}
    return nodes, pairs


def add_node(graph, nodes, nid, ntype, label, detail="", src=""):
    if nid in nodes:
        return nodes[nid]
    n = {"id": nid, "label": label, "type": ntype, "detail": detail}
    if src:
        n["src"] = src
    graph["nodes"].append(n)
    nodes[nid] = n
    return n


def add_link(graph, pairs, source, target, rt, rel, src, **extra) -> bool:
    """Add a link unless the pair is already connected (any verb, either way)."""
    if source == target or (source, target) in pairs:
        return False
    l = {"source": source, "target": target, "rel": rel, "rt": rt, "src": src}
    l.update({k: v for k, v in extra.items() if v})
    graph["links"].append(l)
    pairs.add((source, target))
    pairs.add((target, source))
    return True


# ---------------------------------------------------------------- export times
def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")


def export_times(data_dir: Path) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """first/last ISO time per agent label, page name and ip16 from revisions.jsonl."""
    path = next((p for p in (data_dir / "revisions.jsonl", data_dir / "revisions.jsonl.gz") if p.exists()), None)
    if path is None:
        raise FileNotFoundError(f"revisions.jsonl[.gz] not under {data_dir}")
    out: Dict[str, Dict[str, Tuple[str, str]]] = {"agent": {}, "page": {}, "ip16": {}}

    def upd(kind, key, t):
        if not key:
            return
        f, l = out[kind].get(key, (t, t))
        out[kind][key] = (min(f, t), max(l, t))

    with _open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            t = str(r["time"])
            upd("agent", r.get("label") or "", t)
            upd("page", str(r["page_id"]).split("/", 1)[-1], t)
            upd("ip16", r.get("ip16") or "", t)
    return out


def stamp_times(graph: Dict[str, Any], times: Dict[str, Dict[str, Tuple[str, str]]]) -> int:
    n = 0
    for node in graph["nodes"]:
        key = None
        if node["type"] == "agent":
            key = ("agent", node["label"])
        elif node["type"] == "wikipage":
            key = ("page", node["id"].split(":", 1)[1])
        elif node["type"] == "ip16":
            key = ("ip16", node["id"].split(":", 1)[1])
        if key and key[1] in times[key[0]]:
            node["first"], node["last"] = times[key[0]][key[1]]
            n += 1
    return n


# ---------------------------------------------------------------- reading pack
def _host_index(nodes: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """host -> Atlas node ids that stand for that host (endpoints, proxies, sites)."""
    idx: Dict[str, List[str]] = {}
    for nid, n in nodes.items():
        hosts: Iterable[str] = ()
        if n["type"] in ("endpoint", "platform", "counter", "paste", "board", "proxy"):
            body = nid.split(":", 1)[1]
            if nid in PROXY_HOSTS:
                hosts = PROXY_HOSTS[nid]
            elif "." in body.split("/")[0]:
                hosts = (body.split("/")[0],)
        for h in hosts:
            idx.setdefault(h, []).append(nid)
    return idx


def _match_url(url: str, nodes: Dict[str, Dict[str, Any]], host_idx: Dict[str, List[str]]) -> List[str]:
    """Atlas nodes a URL names: the most specific endpoint/shortener path, else the host node."""
    host = url.split("://", 1)[1].split("/", 1)[0].lower()
    rest = url.split("://", 1)[1]
    hits: List[str] = []
    for nid in host_idx.get(host, []):
        n = nodes[nid]
        body = nid.split(":", 1)[1]
        if n["type"] in ("endpoint",) and "/" in body:
            if rest.startswith(body):
                hits.append(nid)
        elif n["type"] in ("proxy", "platform", "counter", "paste", "board"):
            hits.append(nid)
    # shortener slugs are their own nodes: short:<host>/<slug>
    slug = "short:" + rest.split("?")[0].rstrip("/")
    if slug in nodes:
        hits.append(slug)
    return hits


def pack_edges(graph: Dict[str, Any], pack: Path) -> Dict[str, int]:
    if pack.is_dir():
        pack = pack / "agent-text.sqlite"
    nodes, pairs = _index(graph)
    host_idx = _host_index(nodes)
    pages = {n["id"].split(":", 1)[1]: n["id"] for n in graph["nodes"] if n["type"] == "wikipage"}
    stats = Counter()
    con = sqlite3.connect(f"file:{pack}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT source_group, text FROM documents WHERE source_type='wiki' ORDER BY timestamp_utc, id")
        for group, text in rows:
            name = group.split("/", 1)[-1]
            pid = pages.get(name)
            if pid is None:
                # a page the Atlas does not know is still worth a node when it
                # carries an encoded payload: that is the probe the export hides
                if not _B64.search(text):
                    continue
                pid = add_node(graph, nodes, "page:" + name, "wikipage", name,
                               f"{group.split('/', 1)[0]} page carrying a base64 payload", "pack")["id"]
                pages[name] = pid
                stats["pages_new"] += 1
            stats["body_revisions"] += 1
            for url in set(_URL.findall(text)):
                for nid in _match_url(url, nodes, host_idx):
                    if add_link(graph, pairs, pid, nid, "cites", "cites", "pack"):
                        stats["cites"] += 1
            for payload in set(_B64.findall(text)):
                try:
                    decoded = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "ignore")
                except Exception:
                    continue
                urls = set(_URL.findall(decoded))
                if not urls:
                    continue
                hb = add_node(graph, nodes, "proxy:httpbin-base64", "proxy", "httpbin.org/base64",
                              "echo endpoint used to carry a base64-wrapped link", "pack")
                if add_link(graph, pairs, pid, hb["id"], "cites", "cites (base64 payload)", "pack"):
                    stats["base64_pages"] += 1
                for u in urls:
                    rest = u.split("://", 1)[1].rstrip("/")
                    matched = _match_url(u, nodes, host_idx)
                    if matched:
                        target = matched[0]
                    else:
                        if "end:" + rest not in nodes:
                            stats["base64_endpoints_new"] += 1
                        target = add_node(graph, nodes, "end:" + rest, "endpoint",
                                          rest if len(rest) <= 40 else rest[:39] + "…",
                                          "decoded from a base64 httpbin payload", "pack")["id"]
                    if add_link(graph, pairs, hb["id"], target, "proxies", "decodes to", "pack"):
                        stats["base64_decodes"] += 1
    finally:
        con.close()
    return dict(stats)


# ---------------------------------------------------------------- termina
def fetch_termina(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in TERMINA_FILES:
        urllib.request.urlretrieve(TERMINA_BASE + name, dest / name)  # noqa: S310


def _termina_rt(kind: str, label: str) -> str:
    low = label.lower()
    if kind == "infrastructure":
        if low.startswith("contains"):
            return "hosts"
        if low.startswith(("chain", "proxies")):
            return "proxies"
        return "hosts"
    if kind == "reference":
        return "cites"
    if kind in NEW_RELMETA:
        return kind
    return "links"


def _termina_node_for(vid, venues, graph, nodes, host_idx, stats) -> Optional[str]:
    if vid in TERMINA_ALIASES and TERMINA_ALIASES[vid] in nodes:
        return TERMINA_ALIASES[vid]
    v = venues.get(vid)
    if v is None:
        stats["venue_unknown"] += 1
        return None
    host = (v.get("host") or "").lstrip(".")
    for nid in host_idx.get(host, []):
        return nid
    ntype, prefix = TERMINA_KIND_TYPE.get(v.get("kind") or "", ("endpoint", "end:"))
    label = (host + (v.get("path") or "")).rstrip("/") or vid
    node_id = prefix + vid
    existed = node_id in nodes
    n = add_node(graph, nodes, node_id, ntype, label,
                 f"{v.get('kind')} venue from the termina.digital db ({v.get('status')})", "termina")
    host_idx.setdefault(host, []).append(n["id"])
    if not existed:
        stats["venues_new"] += 1
    return n["id"]


def termina_edges(graph: Dict[str, Any], tdir: Path) -> Dict[str, int]:
    venues = {json.loads(l)["id"]: json.loads(l) for l in (tdir / "venue.jsonl").open() if l.strip()}
    links = [json.loads(l) for l in (tdir / "venue_link.jsonl").open() if l.strip()]
    nodes, pairs = _index(graph)
    host_idx = _host_index(nodes)
    stats = Counter()
    graph.setdefault("relmeta", {}).update(NEW_RELMETA)

    for l in links:
        a = _termina_node_for(l["from_venue"], venues, graph, nodes, host_idx, stats)
        b = _termina_node_for(l["to_venue"], venues, graph, nodes, host_idx, stats)
        if a is None or b is None:
            stats["links_unmapped"] += 1
            continue
        rt = _termina_rt(l.get("kind", ""), l.get("label", ""))
        if add_link(graph, pairs, a, b, rt, l.get("label") or rt, "termina", evidence=l.get("evidence_id")):
            stats["links"] += 1
            stats["rt:" + rt] += 1
        else:
            stats["links_duplicate"] += 1
    return dict(stats)


def termina_database_edges(graph: Dict[str, Any], database: Path) -> Dict[str, int]:
    """Add a compact incident/campaign/cluster layer from the full database."""
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    nodes, pairs = _index(graph)
    host_idx = _host_index(nodes)
    stats = Counter()
    graph.setdefault("relmeta", {}).update({"assigned_to": "assigned to", "observed_at": "observed at"})
    try:
        venues = {row["id"]: dict(row) for row in con.execute("SELECT * FROM venue")}
        incident = dict(con.execute("SELECT * FROM incident WHERE id='dsewiki-2026-05'").fetchone())
        campaign_items = json.loads(incident["campaigns"])
        campaign_ids = [item["id"] if isinstance(item, dict) else item for item in campaign_items]

        def claim_summary(kind: str, subject_id: str) -> str:
            counts = Counter(row[0] for row in con.execute(
                "SELECT status FROM claim WHERE subject_kind=? AND subject_id=?", (kind, subject_id)))
            total = sum(counts.values())
            detail = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
            evidence = con.execute(
                "SELECT count(DISTINCT made_by) FROM claim WHERE subject_kind=? AND subject_id=? AND made_by IS NOT NULL",
                (kind, subject_id),
            ).fetchone()[0]
            return f"{total} claims ({detail}); {evidence} named evidence sources" if total else "no direct claims"

        incident_id = "incident:" + incident["id"]
        incident_existed = incident_id in nodes
        add_node(
            graph, nodes, incident_id, "incident", incident["name"],
            f"{incident['status']} · {incident['severity']} · {claim_summary('incident', incident['id'])}",
            "termina-db",
        )
        if not incident_existed:
            stats["incidents_new"] += 1

        placeholders = ",".join("?" for _ in campaign_ids)
        campaigns = [dict(row) for row in con.execute(
            f"SELECT * FROM campaign WHERE id IN ({placeholders}) ORDER BY id", campaign_ids)]
        clusters = [dict(row) for row in con.execute(
            f"SELECT * FROM cluster WHERE campaign_id IN ({placeholders}) ORDER BY campaign_id,id", campaign_ids)]

        for campaign in campaigns:
            cid = "campaign:" + campaign["id"]
            campaign_existed = cid in nodes
            count, actors, first, last = con.execute(
                "SELECT count(*),count(DISTINCT actor_id),min(observed_time),max(observed_time) FROM record WHERE campaign_id=?",
                (campaign["id"],),
            ).fetchone()
            add_node(
                graph, nodes, cid, "campaign", campaign["name"],
                f"{campaign['kind']} · {campaign['confidence']} · {count:,} records · {actors:,} handles · "
                f"{claim_summary('campaign', campaign['id'])}", "termina-db",
            )
            nodes[cid]["first"], nodes[cid]["last"] = first, last
            if add_link(graph, pairs, incident_id, cid, "contains", "contains", "termina-db"):
                stats["incident_campaign_links"] += 1
            if not campaign_existed:
                stats["campaigns_new"] += 1

        for cluster in clusters:
            cluster_id = "cluster:" + cluster["id"]
            cluster_existed = cluster_id in nodes
            count, actors, first, last = con.execute(
                "SELECT count(*),count(DISTINCT actor_id),min(observed_time),max(observed_time) FROM record WHERE cluster_id=?",
                (cluster["id"],),
            ).fetchone()
            add_node(
                graph, nodes, cluster_id, "cluster", cluster["name"],
                f"{count:,} records · {actors:,} handles · {claim_summary('cluster', cluster['id'])}",
                "termina-db",
            )
            nodes[cluster_id]["first"], nodes[cluster_id]["last"] = first, last
            if add_link(graph, pairs, "campaign:" + cluster["campaign_id"], cluster_id,
                        "contains", "contains", "termina-db"):
                stats["campaign_cluster_links"] += 1
            if not cluster_existed:
                stats["clusters_new"] += 1

            rows = con.execute(
                "SELECT venue_id,count(*) records,count(DISTINCT actor_id) actors "
                "FROM record WHERE cluster_id=? GROUP BY venue_id ORDER BY records DESC",
                (cluster["id"],),
            )
            for row in rows:
                venue_node = _termina_node_for(row["venue_id"], venues, graph, nodes, host_idx, stats)
                if venue_node and add_link(
                    graph, pairs, cluster_id, venue_node, "observed_at",
                    f"{row['records']:,} records · {row['actors']:,} handles", "termina-db"
                ):
                    stats["cluster_venue_links"] += 1

        stats["record_edges_summarized"] = con.execute(
            "SELECT count(*) FROM edge e JOIN record r ON r.id=e.from_record "
            "WHERE r.incident_id='dsewiki-2026-05'"
        ).fetchone()[0]
    finally:
        con.close()
    return dict(stats)


# ---------------------------------------------------------------- main
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--graph", type=Path, default=Path("graph.html"))
    p.add_argument("--export", type=Path, help="collusion.wiki export dir (revisions.jsonl[.gz])")
    p.add_argument("--pack", type=Path, help="agent-reading-pack dir or agent-text.sqlite")
    p.add_argument("--termina", type=Path, help="dir with venue.jsonl + venue_link.jsonl")
    p.add_argument("--fetch-termina", type=Path, metavar="DIR", help="download the termina tables into DIR and exit")
    p.add_argument("--report", type=Path, help="write a JSON provenance report here")
    p.add_argument("--dry-run", action="store_true", help="do not rewrite graph.html")
    a = p.parse_args(argv)

    if a.fetch_termina:
        fetch_termina(a.fetch_termina)
        print(f"fetched {', '.join(TERMINA_FILES)} into {a.fetch_termina}")
        return 0

    html = a.graph.read_text(encoding="utf-8")
    graph, st, end = load_graph(html)
    before = {"nodes": len(graph["nodes"]), "links": len(graph["links"])}
    report: Dict[str, Any] = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "graph": str(a.graph), "before": before, "inputs": {}, "added": {}}

    if a.export:
        times = export_times(a.export)
        report["added"]["stamped_nodes"] = stamp_times(graph, times)
        report["inputs"]["export"] = str(a.export)
    if a.pack:
        sq = a.pack / "agent-text.sqlite" if a.pack.is_dir() else a.pack
        report["added"]["pack"] = pack_edges(graph, a.pack)
        report["inputs"]["pack"] = {"path": str(a.pack), "sha256": _sha(sq)}
    if a.termina:
        report["added"]["termina"] = termina_edges(graph, a.termina)
        database = a.termina / "incidents.sqlite"
        if database.exists():
            report["added"]["termina_database"] = termina_database_edges(graph, database)
        inp = {"path": str(a.termina)}
        for name in TERMINA_FILES:
            f = a.termina / name
            if f.exists():
                inp[name] = _sha(f)
        man = a.termina / "manifest.json"
        if man.exists():
            m = json.loads(man.read_text())
            inp["schema_version"] = m.get("schema_version")
            inp["generated_at"] = m.get("generated_at")
        report["inputs"]["termina"] = inp

    report["after"] = {"nodes": len(graph["nodes"]), "links": len(graph["links"]),
                       "links_by_src": dict(Counter(l.get("src", "atlas") for l in graph["links"])),
                       "nodes_by_type": dict(Counter(n["type"] for n in graph["nodes"]))}
    if not a.dry_run:
        a.graph.write_text(dump_graph(html, graph, st, end), encoding="utf-8")
    if a.report:
        a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
