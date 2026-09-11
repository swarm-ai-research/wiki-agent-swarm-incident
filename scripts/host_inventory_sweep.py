#!/usr/bin/env python3
"""Host-level reverse sweep of the collusion.wiki export.

analysis/signature-sweep.md already runs a reverse sweep, but it filters
candidate URLs by *shape* (`.json`/`.csv`/`/api/`/`/iiif/`/`manifest`/`tesseract`/
`/views/`) because it is hunting data-source targets. That filter structurally
cannot see three classes of surface:

  * a bare proxy or reader host, whose path carries no data-source marker;
  * a tunnel subdomain advertised as a plain root URL;
  * a lookalike or percent-encoded hostname, which is interesting *as a hostname*.

This sweep filters by nothing. It inventories every host the export names,
classifies it, and diffs the inventory against everything this repository has
already catalogued, so "surfaces we hold but never wrote down" become visible.

It also reports two things the host list alone would hide:

  * **percent-encoded hostnames** (`%61llorigins.hexlet.app`), which are
    hostname-parser evasion rather than a typo;
  * **credential-shaped query parameters**, counted per host and never printed --
    the export carries live-looking `token=` values, and this archive's rule is
    that credential-shaped values are counted, not read.

Read-only over data we already hold. Nothing is fetched except the export itself.

  python3 scripts/host_inventory_sweep.py --file revisions.jsonl \
      --catalogue . --out data/host_inventory_sweep_2026-09-09.json
  python3 scripts/host_inventory_sweep.py --report data/host_inventory_sweep_2026-09-09.json
"""
import argparse, collections, hashlib, io, itertools, json, os, re, sys, urllib.request
from pathlib import Path

RAW = ("https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation"
       "/HEAD/agent-logs/prowiki/revisions.jsonl")
UA = "swarm-ai-research/wiki-agent-swarm-incident host sweep (read-only)"

TEXT_FIELDS = ("body", "name", "page_id", "change_summary")

# Generated machine inventories are NOT a catalogue. A host appearing in one only
# means some sweep enumerated it, not that this archive ever wrote it down -- and
# including them lets a sweep's own output silently cancel its next run's
# findings. Excluded from the catalogue diff by default.
GENERATED_INVENTORIES = ("host_inventory_sweep", "shortener_export_crosscheck")

# The farm's own built-in pages. Every cohort writes to them, so they join
# unrelated hosts into one blob -- the same saturation the report documents for
# structural detectors: a shared hub page makes every co-editor a neighbour.
# Excluded from co-occurrence (their revisions are still counted everywhere else).
WIKI_INFRASTRUCTURE_PAGES = frozenset({
    "recentchanges", "startseite", "sandbox", "testseite", "willkommenimwiki",
    "forumseite", "homepage",
})
# Two hosts must share at least this many non-infrastructure revisions to be
# called one family. At 1, a single proxy-menu page fuses everything it lists.
MIN_FAMILY_EDGE = 2
# Page-name tokens too generic to label a family with.
LABEL_STOPWORDS = frozenset(
    "agent agents test tests page pages links link ref refs source src data new "
    "fresh helper research wiki one two three".split())
# Hostnames may carry percent-escapes; that is the point, so % is in the class.
HOST_RE = re.compile(r"https?://([A-Za-z0-9._~%-]+\.[A-Za-z]{2,})(?::\d+)?", re.I)
ENCODED_HOST_RE = re.compile(r"%[0-9A-Fa-f]{2}")

# Query parameters whose values look like credentials. Values are hashed, never kept.
CRED_PARAM_RE = re.compile(
    r"[?&](token|api_?key|access_?token|auth|secret|signature|sig)=([^&\s\]\"'|]{6,})", re.I)

# Ordered: first match wins. Built from surfaces already named in this archive.
CLASSES = (
    ("tunnel", r"(?:^|\.)(?:run\.pinggy-free\.link|pinggy\.link|serveo(?:usercontent)?\.net|"
               r"serveousercontent\.com|localtunnel\.me|loca\.lt|ngrok\.io)$"),
    ("translate_proxy", r"(?:\.translate\.goog$|^translate(?:-pa)?\.google(?:apis)?\.com$)"),
    ("archive_proxy", r"(?:^web\.archive\.org$|^wayback\.archive\.org$|^memgator\.|"
                      r"^index\.commoncrawl\.org$|\.preservica\.com$)"),
    ("reader_proxy", r"(?:^r\.jina|^pure\.md$|^md\.|^markdown\.|^platform\.lemino\.ai$|"
                     r"^.*\burl2md\b|^md\d*\.|\.succ\.ai$|^microlink\.|^markdown\.microlink\.io$)"),
    ("cors_proxy", r"(?:cors|allorigins|thingproxy|jsonp|proxy)"),
    ("cloud_blob", r"\.blob\.core\.windows\.net$"),
    ("shortener", r"^(?:vanderbi\.lt|uoft\.me|rmn\.re|bitily\.in|2dd\.pl|is\.gd|v\.gd|"
                  r"da\.gd|tinyurl\.com|u\.ethz\.ch)$"),
    ("wiki_host", r"(?:wikiservice|prowiki|dorfwiki|ludism|usemod|texteditors|tmcleod)"),
)


def read_export(path=None, url=RAW):
    if path:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as resp:
        for raw in io.TextIOWrapper(resp, encoding="utf-8", errors="replace"):
            if raw.strip():
                yield json.loads(raw)


def classify(host):
    for name, pattern in CLASSES:
        if re.search(pattern, host, re.I):
            return name
    return "endpoint"


def scan(revisions):
    hosts = collections.Counter()
    first_seen, last_seen = {}, {}
    pages = collections.defaultdict(set)
    page_counts = collections.defaultdict(collections.Counter)
    page_index = collections.defaultdict(
        lambda: {"revisions": 0, "hosts": collections.Counter(),
                 "labels": collections.Counter(), "times": []})
    cooccurrence = collections.Counter()
    infrastructure_revs = collections.Counter()
    # Per-revision host sets, so "names an uncatalogued host" can be counted once
    # the catalogue is known. Counting "names any host" instead is near-vacuous:
    # wikiservice.at appears in almost every body.
    infrastructure_host_sets = collections.defaultdict(list)
    encoded = collections.Counter()
    creds = collections.defaultdict(lambda: {"occurrences": 0, "distinct": set()})
    n = 0
    for rev in revisions:
        n += 1
        blob = " ".join(str(rev.get(f) or "") for f in TEXT_FIELDS)
        when, name = rev.get("time") or "", rev.get("name") or ""
        # One key for both indexes; a bare name collides across the farm's wikis.
        page_key = rev.get("page_id") or name
        is_infrastructure = name.lower() in WIKI_INFRASTRUCTURE_PAGES
        if "://" not in blob:
            # Still a revision on that page, just one naming no host.
            if is_infrastructure:
                infrastructure_revs[name] += 1
                infrastructure_host_sets[name].append(frozenset())
            continue
        found = set()
        for host in HOST_RE.findall(blob):
            host = host.lower()
            found.add(host)
            hosts[host] += 1
            if ENCODED_HOST_RE.search(host):
                encoded[host] += 1
            if when and (host not in first_seen or when < first_seen[host]):
                first_seen[host] = when
            if when and (host not in last_seen or when > last_seen[host]):
                last_seen[host] = when
            if len(pages[host]) < 12:
                pages[host].add(name)
            page_counts[host][page_key] += 1
        if found and not is_infrastructure:
            entry = page_index[page_key]
            entry["revisions"] += 1
            entry["hosts"].update(found)
            entry["labels"][rev.get("label") or "?"] += 1
            if when:
                entry["times"].append(when)
        if is_infrastructure:
            infrastructure_revs[name] += 1
            infrastructure_host_sets[name].append(frozenset(found))
        else:
            for pair in itertools.combinations(sorted(found), 2):
                cooccurrence[pair] += 1
        for param, value in CRED_PARAM_RE.findall(blob):
            # Attribute to the nearest preceding host, else to the parameter alone.
            key = param.lower()
            creds[key]["occurrences"] += 1
            creds[key]["distinct"].add(hashlib.sha256(value.encode()).hexdigest()[:12])
    return {
        "revisions": n, "hosts": hosts, "first_seen": first_seen,
        "last_seen": last_seen, "cooccurrence": cooccurrence,
        "page_counts": page_counts, "infrastructure_revisions": infrastructure_revs,
        "page_index": page_index,
        "infrastructure_host_sets": dict(infrastructure_host_sets),
        "pages": {h: sorted(v) for h, v in pages.items()},
        "encoded_hosts": encoded,
        "credential_params": {k: {"occurrences": v["occurrences"],
                                  "distinct_values": len(v["distinct"])}
                              for k, v in sorted(creds.items())},
    }


def catalogue_text(root, skip_names=()):
    """Concatenate every catalogue file, skipping this sweep's own outputs."""
    exts = (".md", ".html", ".json", ".js", ".py", ".txt", ".jsonl", ".csv")
    chunks = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for filename in filenames:
            if not filename.endswith(exts) or any(s in filename for s in skip_names):
                continue
            try:
                chunks.append(Path(dirpath, filename).read_text(
                    encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(chunks).lower()


def families(scanned, hosts_of_interest):
    """Cluster hosts that co-occur on ordinary (non-infrastructure) pages.

    Union-find over co-occurrence edges of weight >= MIN_FAMILY_EDGE. A family is
    a set of hosts repeatedly named together by the same task pages -- evidence
    they served one task, not merely that one page listed them both once.
    """
    parent = {h: h for h in hosts_of_interest}

    def find(host):
        while parent[host] != host:
            parent[host] = parent[parent[host]]
            host = parent[host]
        return host

    for (left, right), weight in scanned["cooccurrence"].items():
        if weight >= MIN_FAMILY_EDGE and left in parent and right in parent:
            parent[find(left)] = find(right)

    groups = collections.defaultdict(list)
    for host in hosts_of_interest:
        groups[find(host)].append(host)

    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        tokens = collections.Counter()
        seen_pages = collections.Counter()
        for host in members:
            for page, count in scanned["page_counts"].get(host, {}).items():
                leaf = page.rsplit("/", 1)[-1]
                if leaf.lower() in WIKI_INFRASTRUCTURE_PAGES:
                    continue
                seen_pages[page] += count
                for token in re.findall(r"[A-Z][a-z]+", leaf):
                    if token.lower() not in LABEL_STOPWORDS:
                        tokens[token] += count
        firsts = [scanned["first_seen"][h] for h in members if h in scanned["first_seen"]]
        lasts = [scanned["last_seen"][h] for h in members if h in scanned["last_seen"]]
        out.append({
            "label": "/".join(t for t, _ in tokens.most_common(5)),
            "hosts": sorted(members, key=lambda h: -scanned["hosts"][h]),
            "occurrences": sum(scanned["hosts"][h] for h in members),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            "example_pages": [p for p, _ in seen_pages.most_common(5)],
        })
    return sorted(out, key=lambda f: -f["occurrences"])


def baseline_hosts(path):
    """Uncatalogued host list recorded by an earlier run.

    Documenting a host makes it catalogued -- that is the point of the diff, and
    it means this sweep shrinks its own next result. Good for the coverage
    metric, bad for clustering: families computed against a moving set are not
    reproducible, and the first hosts written up drop out of their own families.
    Pin the clustering to the run that found them.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row["host"] for row in data.get("hosts", []) if row.get("catalogued") is False}


def page_anchors(scanned, hosts, clustered):
    """Reconstruct a task around each host that co-occurrence could not cluster.

    families() clusters only hosts that are themselves uncatalogued, so a host
    whose task-mates are already catalogued falls out as a singleton even when it
    anchors a busy page. Anchoring on the page instead recovers those: report the
    page a host appears on most, with the page's revision count, span, labels and
    *all* co-hosts -- catalogued ones included, since those are exactly the
    context the host-only view discards.

    Hostnames, page names and labels only. Bodies are never carried out: the
    export's page bodies hold credential-shaped values.
    """
    out = []
    for host in sorted(hosts - clustered):
        candidates = [(page, count) for page, count
                      in scanned["page_counts"].get(host, {}).items()
                      if page.rsplit("/", 1)[-1].lower() not in WIKI_INFRASTRUCTURE_PAGES]
        if not candidates:
            continue
        # Prefer the busiest page this host appears on, then the most revisions.
        best = max(candidates, key=lambda pc: (
            scanned["page_index"].get(pc[0], {}).get("revisions", 0), pc[1]))[0]
        entry = scanned["page_index"].get(best)
        if not entry:
            continue
        times = sorted(entry["times"])
        out.append({
            "host": host,
            "anchor_page": best,
            "page_revisions": entry["revisions"],
            "first_seen": times[0] if times else None,
            "last_seen": times[-1] if times else None,
            "labels": [l for l, _ in entry["labels"].most_common(4)],
            "co_hosts": [h for h, _ in entry["hosts"].most_common(8) if h != host],
        })
    return sorted(out, key=lambda a: -a["page_revisions"])


def build(scanned, catalogue=None, with_families=False, family_hosts=None):
    hosts = scanned["hosts"]
    rows = []
    for host, count in hosts.most_common():
        rows.append({
            "host": host, "occurrences": count, "class": classify(host),
            "first_seen": scanned["first_seen"].get(host),
            "encoded_hostname": bool(ENCODED_HOST_RE.search(host)),
            "example_pages": scanned["pages"].get(host, [])[:6],
            "catalogued": (host in catalogue) if catalogue is not None else None,
        })
    by_class = collections.Counter(r["class"] for r in rows)
    result = {
        "revisions_scanned": scanned["revisions"],
        "distinct_hosts": len(hosts),
        "hosts_by_class": dict(by_class.most_common()),
        "encoded_hostnames": dict(scanned["encoded_hosts"].most_common()),
        "credential_params": scanned["credential_params"],
        "hosts": rows,
    }
    if catalogue is not None:
        new = [r for r in rows if not r["catalogued"]]
        result["uncatalogued_count"] = len(new)
        result["uncatalogued_by_class"] = dict(
            collections.Counter(r["class"] for r in new).most_common())
        if with_families:
            pinned = family_hosts if family_hosts is not None else {
                r["host"] for r in new}
            result["family_host_count"] = len(pinned)
            result["family_hosts_pinned"] = family_hosts is not None
            result["infrastructure_revisions"] = dict(
                scanned["infrastructure_revisions"].most_common())
            result["infrastructure_uncatalogued_revisions"] = {
                page: sum(1 for hosts in sets if hosts & pinned)
                for page, sets in sorted(
                    scanned["infrastructure_host_sets"].items(),
                    key=lambda kv: -len(kv[1]))}
            result["families"] = families(scanned, pinned)
            clustered = {h for f in result["families"] for h in f["hosts"]}
            result["page_anchors"] = page_anchors(scanned, pinned, clustered)
    return result


def report(data):
    print(f"revisions scanned   {data['revisions_scanned']}")
    print(f"distinct hosts      {data['distinct_hosts']}")
    if "uncatalogued_count" in data:
        print(f"not in catalogue    {data['uncatalogued_count']}")
    print("\nby class" + (" (uncatalogued in brackets)" if "uncatalogued_by_class" in data else ""))
    unc = data.get("uncatalogued_by_class", {})
    for name, count in data["hosts_by_class"].items():
        extra = f"  [{unc.get(name, 0)}]" if unc else ""
        print(f"  {name:<16} {count:>4}{extra}")
    if data["encoded_hostnames"]:
        print("\npercent-encoded hostnames (parser evasion)")
        for host, count in data["encoded_hostnames"].items():
            print(f"  {count:>4}  {host}")
    if data.get("families"):
        pin = " (pinned to baseline)" if data.get("family_hosts_pinned") else ""
        print(f"\ncandidate task families among {data.get('family_host_count')} "
              f"uncatalogued hosts{pin} -- {len(data['families'])} families")
        for fam in data["families"]:
            span = f"{(fam['first_seen'] or '')[:10]}..{(fam['last_seen'] or '')[:10]}"
            print(f"  [{fam['occurrences']:>4}] {span}  {fam['label']}")
            for host in fam["hosts"]:
                print(f"           {host}")
    if data.get("page_anchors"):
        print(f"\npage-anchored tasks for the {len(data['page_anchors'])} hosts "
              f"co-occurrence could not cluster (top 12 by page size)")
        for anchor in data["page_anchors"][:12]:
            print(f"  {anchor['host']}")
            print(f"     {anchor['anchor_page']}  "
                  f"({anchor['page_revisions']} revs, "
                  f"{(anchor['first_seen'] or '')[:10]}..{(anchor['last_seen'] or '')[:10]})")
            if anchor["co_hosts"]:
                print(f"     alongside {', '.join(anchor['co_hosts'][:5])}")
    if data.get("infrastructure_revisions"):
        unc = data.get("infrastructure_uncatalogued_revisions", {})
        print(f"\nthe farm's own pages (excluded from co-occurrence): "
              f"{sum(data['infrastructure_revisions'].values())} revisions, "
              f"{sum(unc.values())} naming an uncatalogued host")
        for page, count in data["infrastructure_revisions"].items():
            print(f"  {count:>5} revs  {unc.get(page, 0):>4} uncat.  {page}")
    if data["credential_params"]:
        print("\ncredential-shaped parameters (counted, never printed)")
        for param, info in data["credential_params"].items():
            print(f"  {param:<14} {info['occurrences']:>4} occurrences, "
                  f"{info['distinct_values']} distinct values")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="local revisions.jsonl (default: stream from GitHub)")
    ap.add_argument("--catalogue", help="repo root to diff the inventory against")
    ap.add_argument("--families", action="store_true",
                    help="cluster uncatalogued hosts into candidate task families "
                         "(requires --catalogue)")
    ap.add_argument("--baseline",
                    help="pin --families to the uncatalogued hosts recorded by an "
                         "earlier run, so writing them up does not dissolve them")
    ap.add_argument("--out", help="write the full result as JSON")
    ap.add_argument("--report", help="print a saved result instead of rescanning")
    args = ap.parse_args(argv)

    if args.report:
        report(json.loads(Path(args.report).read_text(encoding="utf-8")))
        return 0

    catalogue = None
    if args.catalogue:
        catalogue = catalogue_text(args.catalogue,
                                   skip_names=GENERATED_INVENTORIES)
    if args.families and not args.catalogue:
        ap.error("--families requires --catalogue")
    pinned = baseline_hosts(args.baseline) if args.baseline else None
    data = build(scan(read_export(args.file)), catalogue,
                 with_families=args.families, family_hosts=pinned)
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
