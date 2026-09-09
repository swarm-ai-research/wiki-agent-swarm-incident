#!/usr/bin/env python3
"""Cross-check a shortener link corpus against the collusion.wiki export.

Reproduces, from our own copy of the export, the export side of the two figures
this archive has been quoting from the brausepulver shortener audit
(analysis/sub-swarms.md): "the wiki cites 23 of 5,660 short links" and "89 of the
148 target hosts the shorteners reach never appear in the wiki dump at all".

Both are set operations over two inventories:

  A. every host and every shortener alias the EXPORT names   <- computed here
  B. every link and target host the SHORTENERS hold          <- supplied by --links

Side A is fully reproducible from the public export and needs no network beyond
fetching it. Side B is not: it comes from YOURLS statistics pages, and the audit
that enumerated it is linked, not re-hosted. So this script computes A on its
own, and folds in B only when a link list is supplied. Run it with no --links to
get the export-side inventory; run it with --links to get the "N of M" answers.

Read-only. It never resolves a short link -- following a bare short URL registers
a click and mutates the statistics being cited. Only the export is fetched.

  python3 scripts/shortener_export_crosscheck.py --out data/shortener_export_crosscheck.json
  python3 scripts/shortener_export_crosscheck.py --file revisions.jsonl --out out.json
  python3 scripts/shortener_export_crosscheck.py --links vanderbi_links.json --out out.json
  python3 scripts/shortener_export_crosscheck.py --report data/shortener_export_crosscheck.json

--links accepts JSON (a list of objects, or {"links": [...]}) or CSV, with an
alias field named keyword/alias/short/shorturl and a target field named
url/target/long/longurl. Extra fields are ignored.
"""
import argparse, collections, csv, io, json, re, sys, urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation/HEAD/agent-logs/prowiki/revisions.jsonl"
UA = "swarm-ai-research/wiki-agent-swarm-incident crosscheck (read-only)"

# Fields whose text can carry a URL. Page names and change summaries matter:
# most ProbierWiki bodies in the export are the 27-byte placeholder.
TEXT_FIELDS = ("body", "name", "page_id", "change_summary")

HOST_RE = re.compile(r"https?://([A-Za-z0-9._~%-]+\.[A-Za-z]{2,})(?::\d+)?", re.I)

# Shortener hosts named anywhere in this archive's surface inventory.
SHORTENER_HOSTS = (
    "vanderbi.lt", "uoft.me", "rmn.re", "bitily.in", "yourls.pro",
    "yourls.website", "yourls.biz", "2dd.pl", "u.ethz.ch",
    "is.gd", "v.gd", "da.gd", "tinyurl.com",
)
_HOST_ALT = "|".join(h.replace(".", r"\.") for h in SHORTENER_HOSTS)
# Plain form: host/alias. Excludes the script paths so they are not read as aliases.
ALIAS_RE = re.compile(
    rf"(?:https?://)?({_HOST_ALT})/(?!yourls-go\.php|yourls-api\.php|admin\b)"
    rf"([A-Za-z0-9_-]{{1,60}})", re.I)
# YOURLS redirector form: host/yourls-go.php?id=alias
GO_RE = re.compile(
    rf"(?:https?://)?({_HOST_ALT})/yourls-go\.php\?id=([A-Za-z0-9_-]{{1,60}})", re.I)


def read_export(path=None, url=RAW):
    """Yield revision dicts from a local export file or by streaming the raw URL."""
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


def scan_export(revisions):
    """Inventory every host and shortener alias the export names."""
    hosts = collections.Counter()
    aliases = collections.defaultdict(collections.Counter)
    n = 0
    for rev in revisions:
        n += 1
        blob = " ".join(str(rev.get(f) or "") for f in TEXT_FIELDS)
        if "://" not in blob and "." not in blob:
            continue
        for host in HOST_RE.findall(blob):
            hosts[host.lower()] += 1
        for regex in (GO_RE, ALIAS_RE):
            for host, alias in regex.findall(blob):
                aliases[host.lower()][alias] += 1
    return {"revisions": n, "hosts": hosts, "aliases": aliases}


def _norm_host(value):
    """Bare hostname from a URL or host string, lowercased, no leading www."""
    if not value:
        return ""
    value = str(value).strip()
    m = HOST_RE.match(value if "://" in value else "http://" + value)
    host = m.group(1).lower() if m else ""
    return host[4:] if host.startswith("www.") else host


def load_links(path):
    """Read a shortener link list (JSON or CSV) into [{'alias':..,'target':..}]."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    rows = []
    if text.lstrip()[:1] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("links", "results", "rows", "data"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:  # a keyword -> record mapping
                data = [dict(v, keyword=k) if isinstance(v, dict) else
                        {"keyword": k, "url": v} for k, v in data.items()]
        rows = [r for r in data if isinstance(r, dict)]
    else:
        rows = list(csv.DictReader(io.StringIO(text)))

    out = []
    for row in rows:
        low = {str(k).strip().lower(): v for k, v in row.items()}
        alias = next((low[k] for k in ("keyword", "alias", "short", "shorturl",
                                       "shorturl_keyword") if low.get(k)), "")
        target = next((low[k] for k in ("url", "target", "long", "longurl",
                                        "original", "originalurl") if low.get(k)), "")
        # A YOURLS statistics page is the alias plus a trailing "+".
        alias = str(alias).strip().rsplit("/", 1)[-1].rstrip("+").strip()
        if alias or target:
            out.append({"alias": alias, "target": str(target).strip(),
                        "target_host": _norm_host(target)})
    return out


def crosscheck(scan, links):
    """Answer 'N of M' for target hosts and for aliases, against the export."""
    export_hosts = {h[4:] if h.startswith("www.") else h for h in scan["hosts"]}
    cited = {h: set(a) for h, a in scan["aliases"].items()}

    hosts = sorted({l["target_host"] for l in links if l["target_host"]})
    seen = sorted(h for h in hosts if h in export_hosts)
    unseen = sorted(h for h in hosts if h not in export_hosts)

    alias_seen, alias_total = [], 0
    for link in links:
        if not link["alias"]:
            continue
        alias_total += 1
        if any(link["alias"] in v for v in cited.values()):
            alias_seen.append(link["alias"])

    return {
        "links_supplied": len(links),
        "target_hosts": len(hosts),
        "target_hosts_in_export": len(seen),
        "target_hosts_absent_from_export": len(unseen),
        "aliases_supplied": alias_total,
        "aliases_cited_in_export": len(set(alias_seen)),
        "hosts_absent": unseen,
        "hosts_present": seen,
        "aliases_cited": sorted(set(alias_seen)),
    }


def build(scan, links=None):
    result = {
        "revisions_scanned": scan["revisions"],
        "distinct_hosts_in_export": len(scan["hosts"]),
        "export_hosts": dict(scan["hosts"].most_common()),
        "shortener_aliases_in_export": {
            host: {"distinct": len(counter), "occurrences": sum(counter.values()),
                   "aliases": dict(counter.most_common())}
            for host, counter in sorted(scan["aliases"].items())
        },
    }
    if links is not None:
        result["crosscheck"] = crosscheck(scan, links)
    return result


def report(data):
    print(f"revisions scanned            {data['revisions_scanned']}")
    print(f"distinct hosts in export     {data['distinct_hosts_in_export']}")
    print("\nshortener aliases cited by the export")
    for host, info in data["shortener_aliases_in_export"].items():
        print(f"  {host:<14} {info['distinct']:>4} distinct  "
              f"{info['occurrences']:>5} occurrences")
    cc = data.get("crosscheck")
    if not cc:
        print("\n(no --links supplied: shortener side not computed)")
        return
    print(f"\ncrosscheck against {cc['links_supplied']} supplied links")
    print(f"  target hosts                 {cc['target_hosts']}")
    print(f"  ...named in the export       {cc['target_hosts_in_export']}")
    print(f"  ...absent from the export    {cc['target_hosts_absent_from_export']}")
    print(f"  aliases cited by the export  {cc['aliases_cited_in_export']} "
          f"of {cc['aliases_supplied']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="local revisions.jsonl (default: stream from GitHub)")
    ap.add_argument("--links", help="shortener link list (JSON or CSV)")
    ap.add_argument("--out", help="write the full result as JSON")
    ap.add_argument("--report", help="print a saved result instead of rescanning")
    args = ap.parse_args(argv)

    if args.report:
        report(json.loads(Path(args.report).read_text(encoding="utf-8")))
        return 0

    links = load_links(args.links) if args.links else None
    data = build(scan_export(read_export(args.file)), links)
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=1, sort_keys=False) + "\n",
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
