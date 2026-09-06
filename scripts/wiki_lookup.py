#!/usr/bin/env python3
"""Regex lookups for the wiki agent-swarm incident.

Two modes, both read-only:

  probe   fetch RecentChanges (or any URL) for a list of candidate wikis and
          report which signature patterns hit, with context.
  grep    stream the public forensic export (JoshuaDavid/WikiAgentSwarmInvestigation
          agent-logs/*.jsonl) and print revisions whose title, label, summary or
          body match a regex.
  owi     look for swarm activity in the Open Web Index (openwebindex.eu), the
          European open crawl that indexes hosts Google and Bing have dropped.
          Three sub-modes: `terms` prints the literal query set (handles, page-name
          prefixes, proxy hosts, wiki hosts) for a MOSAIC search box; `search` runs
          those terms through a MOSAIC REST endpoint (/search?q=, JSON or
          OpenSearch XML) and keeps results that land on a known wiki host or
          match a signature; `sql` runs (or prints) the DuckDB query that scans
          downloaded OWI parquet shards (owilix) for the same set.

Examples
  python3 scripts/wiki_lookup.py probe                       # all candidates, default signatures
  python3 scripts/wiki_lookup.py probe --only ludism,usemod  # subset by id substring
  python3 scripts/wiki_lookup.py probe --url "https://example.org/wiki.cgi?action=rc" -e 'Agent[A-Z]'
  python3 scripts/wiki_lookup.py probe --json out.json       # machine-readable result
  python3 scripts/wiki_lookup.py grep 'ZZZ[A-Z]' --wikis dse,probier --field title
  python3 scripts/wiki_lookup.py grep 'blob\\.core\\.windows\\.net' --field body --limit 20
  python3 scripts/wiki_lookup.py signatures                  # print the default pattern set
  python3 scripts/wiki_lookup.py owi terms                   # query set for a MOSAIC search box
  python3 scripts/wiki_lookup.py owi search --json owi.json  # public demo, https://mosaic.ows.eu
  python3 scripts/wiki_lookup.py owi search --base http://localhost:8008 --only hosts
  python3 scripts/wiki_lookup.py owi sql --parquet 'owi/**/*.parquet'   # needs duckdb
  python3 scripts/wiki_lookup.py owi sql --print             # just the SQL + owilix recipe

Nothing here writes to any wiki. Candidates, signatures and the Open Web Index
query terms live in data/wiki_candidates.json so they can be extended without
touching code.
"""
import argparse, collections, html, json, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "wiki_candidates.json"
RAW = "https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation/HEAD/agent-logs/"
UA = "Mozilla/5.0 (research; swarm-ai-research incident archive; read-only)"
# The combined `prowiki` cut carries revision bodies for dse/probier/fractal/dorfwiki;
# the per-wiki files for those four are metadata-only live scrapes. Small wikis have
# their own files. So a request for dse/probier/fractal/dorfwiki reads `prowiki`
# and filters on the record's `wiki` field.
PROWIKI_CUT = {"dse", "probier", "fractal", "dorfwiki"}
SMALL_WIKIS = ["wiki4d", "apchem", "texteditors", "ludism", "milkwiki"]
EXPORT_WIKIS = sorted(PROWIKI_CUT) + SMALL_WIKIS


def load_cfg():
    return json.loads(CFG.read_text())


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore") if e.fp else ""
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))


def compile_sigs(patterns):
    return [(p, re.compile(p)) for p in patterns]


def probe_one(name, url, sigs, ctx):
    code, body = fetch(url)
    text = strip(body)
    hits = collections.Counter()
    samples = []
    for pat, rx in sigs:
        for m in rx.finditer(text):
            hits[pat] += 1
            if len(samples) < ctx:
                a, b = max(0, m.start() - 80), min(len(text), m.end() + 80)
                samples.append(text[a:b].strip())
    bot = bool(re.search(r"Are you Human|bot check|captcha", body, re.I))
    return {"id": name, "url": url, "http": code, "bytes": len(body), "bot_check": bot,
            "hits": dict(hits.most_common()), "samples": samples}


def cmd_probe(a):
    cfg = load_cfg()
    sigs = compile_sigs(a.expr or cfg["signatures"])
    targets = [(c["id"], c["url"]) for c in cfg["candidates"]]
    if a.url:
        targets = [("custom", u) for u in a.url]
    if a.only:
        keys = a.only.split(",")
        targets = [t for t in targets if any(k in t[0] for k in keys)]
    results = []
    for name, url in targets:
        r = probe_one(name, url, sigs, a.context)
        results.append(r)
        flag = "BOT" if r["bot_check"] else ("HIT" if r["hits"] else "-")
        top = ", ".join(f"{k} x{v}" for k, v in list(r["hits"].items())[:5])
        print(f"{name:26s} {r['http']:>3} {r['bytes']:>8}B {flag:3s} {top}", flush=True)
        if a.verbose:
            for s in r["samples"]:
                print("      …", s[:200])
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))
        print(f"wrote {a.json}", file=sys.stderr)


def iter_export(wiki):
    req = urllib.request.Request(RAW + f"{wiki}/revisions.jsonl", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def cmd_grep(a):
    rx = re.compile(a.pattern, re.I if a.ignore_case else 0)
    fields = a.field.split(",")
    n = 0
    per_wiki = collections.Counter()
    wanted = a.wikis.split(",") if a.wikis else EXPORT_WIKIS
    files = (["prowiki"] if PROWIKI_CUT & set(wanted) else []) + [w for w in wanted if w not in PROWIKI_CUT]
    for src in files:
        for rev in iter_export(src):
            wiki = rev.get("wiki") or src
            if wiki not in wanted:
                continue
            hay = " ".join(str(rev.get(f) or "") for f in fields)
            m = rx.search(hay)
            if not m:
                continue
            per_wiki[wiki] += 1
            n += 1
            if n <= a.limit:
                a0, b0 = max(0, m.start() - 60), min(len(hay), m.end() + 60)
                print(f"{rev.get('wiki')}\t{(rev.get('time') or '')[:16]}\t{rev.get('label')}\t{rev.get('name')}\t…{hay[a0:b0].strip()}…")
    print(f"\n{n} matching revisions: {dict(per_wiki)}", file=sys.stderr)


# --- Open Web Index (openwebindex.eu) ---------------------------------------
# MOSAIC (mosaic.ows.eu, opencode.it4i.eu/openwebsearcheu-public/mosaic) is the
# OpenWebSearch.eu search service: Lucene over an index partition, a REST API at
# /search?q=<terms> (proprietary JSON or OpenSearch XML) and /index-info. owilix is
# the CLI that pulls OWI parquet shards and runs DuckDB SQL over them (columns
# include url, title, plain_text, domain_label, warc_date). Both are read-only.
OWI_DEFAULT_BASE = "https://mosaic.ows.eu"
OWI_URL_KEYS = ("url", "uri", "link", "href", "id")
OWI_TEXT_KEYS = ("title", "snippet", "text", "plain_text", "content", "description", "summary", "abstract")


def host_of(url):
    try:
        h = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return h.lower().removeprefix("www.")


def owi_terms(cfg):
    """Literal query terms: the configured set plus every candidate wiki host."""
    o = cfg.get("owi", {})
    hosts = []
    for c in cfg["candidates"]:
        h = host_of(c["url"])
        if h and h not in hosts:
            hosts.append(h)
    for h in o.get("extra_hosts", []):
        if h not in hosts:
            hosts.append(h)
    return {"signatures": list(o.get("terms", [])), "hosts": hosts}


def owi_walk(obj):
    """Yield every dict in a JSON tree that carries a URL-ish field."""
    if isinstance(obj, dict):
        if any(isinstance(obj.get(k), str) and obj[k].startswith("http") for k in OWI_URL_KEYS):
            yield obj
        for v in obj.values():
            yield from owi_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from owi_walk(v)


def owi_parse(body):
    """Normalise a MOSAIC response (JSON or OpenSearch/RSS/Atom XML) to
    [{url, title, text}]. Tolerant of unknown field names: the JSON format is
    documented as proprietary, so any dict with a URL field is a result."""
    body = body.strip()
    out = []
    if body.startswith("{") or body.startswith("["):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return out
        for d in owi_walk(data):
            url = next(d[k] for k in OWI_URL_KEYS if isinstance(d.get(k), str) and d[k].startswith("http"))
            title = next((str(d[k]) for k in ("title", "name") if d.get(k)), "")
            text = " ".join(str(d[k]) for k in OWI_TEXT_KEYS if d.get(k))
            out.append({"url": url, "title": title, "text": text})
        return out
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return out
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        url, title, text = "", "", []
        for c in el:
            ct = c.tag.rsplit("}", 1)[-1]
            if ct == "link":
                url = (c.text or "").strip() or c.get("href", "")
            elif ct == "title":
                title = (c.text or "").strip()
            elif ct in ("description", "summary", "content"):
                text.append(c.text or "")
        if url:
            out.append({"url": url, "title": title, "text": strip(" ".join(text))})
    return out


def owi_query(base, path, term, fmt, size, timeout):
    q = {"q": term}
    if size:
        q["size"] = str(size)
    if fmt == "xml":
        q["format"] = "opensearch"
    url = base.rstrip("/") + path + "?" + urllib.parse.urlencode(q)
    code, body = fetch(url, timeout=timeout)
    return url, code, body


def cmd_owi_terms(a):
    t = owi_terms(load_cfg())
    print("# signature terms (handles, page-name prefixes, proxy/data hosts)")
    for x in t["signatures"]:
        print(x)
    print("# wiki hosts (from the candidate list)")
    for h in t["hosts"]:
        print(h)


def cmd_owi_search(a):
    cfg = load_cfg()
    t = owi_terms(cfg)
    sigs = compile_sigs(cfg["signatures"])
    hosts = set(t["hosts"])
    terms = []
    if a.only in ("all", "signatures"):
        terms += t["signatures"]
    if a.only in ("all", "hosts"):
        terms += t["hosts"]
    if a.term:
        terms = a.term
    results, blocked = [], 0
    for term in terms:
        url, code, body = owi_query(a.base, a.path, term, a.format, a.size, a.timeout)
        rows = owi_parse(body) if code == 200 else []
        kept = []
        for r in rows:
            on_wiki = host_of(r["url"]) in hosts
            hay = f"{r['url']} {r['title']} {r['text']}"
            hit = [p for p, rx in sigs if rx.search(hay)]
            if on_wiki or hit:
                kept.append({**r, "on_wiki_host": on_wiki, "signature_hits": hit})
        results.append({"term": term, "request": url, "http": code, "results": len(rows), "kept": kept,
                        "error": body[:200] if code != 200 else ""})
        if code in (0, 403, 407):
            blocked += 1
        print(f"{term:40.40s} http={code:>3} results={len(rows):>4} kept={len(kept):>3}", flush=True)
        for k in kept[: a.limit]:
            tag = "WIKI" if k["on_wiki_host"] else "SIG "
            print(f"      {tag} {k['url']}  {k['title'][:80]}  {','.join(k['signature_hits'][:3])}")
        if code != 200 and a.verbose:
            print(f"      ! {body[:160]}")
    total = sum(len(r["kept"]) for r in results)
    print(f"\n{len(results)} queries, {total} kept results", file=sys.stderr)
    if blocked == len(results) and results:
        print("every request failed with a connect/403/407 error: the index host is unreachable "
              "from this network (egress policy), not empty — run from a host that can reach it",
              file=sys.stderr)
    if a.json:
        Path(a.json).write_text(json.dumps({"base": a.base, "path": a.path, "queries": results}, indent=1))
        print(f"wrote {a.json}", file=sys.stderr)


def owi_sql(cfg, parquet, limit):
    """DuckDB SQL over OWI shards: rows on a known wiki host, or whose text/url
    carries a signature term. `regexp_matches` uses RE2 syntax, so the Python
    signature set is passed as literal terms (owi.terms), not the regexes."""
    t = owi_terms(cfg)
    hosts = ", ".join(f"'{h}'" for h in t["hosts"])
    # RE2 rejects some of re.escape's escapes (e.g. "\\ "), so escape only metacharacters.
    terms = "|".join(re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", x) for x in t["signatures"])
    return f"""-- Open Web Index shard scan for wiki agent-swarm signatures (read-only)
WITH docs AS (
  SELECT url, title, plain_text, domain_label, warc_date,
         regexp_replace(lower(regexp_extract(url, '^https?://([^/:]+)', 1)), '^www\\.', '') AS host
  FROM read_parquet('{parquet}')
)
SELECT host, url, warc_date, title,
       host IN ({hosts}) AS on_wiki_host,
       regexp_extract(url || ' ' || coalesce(title, '') || ' ' || coalesce(plain_text, ''), '({terms})', 1) AS signature
FROM docs
WHERE host IN ({hosts})
   OR regexp_matches(url || ' ' || coalesce(title, '') || ' ' || coalesce(plain_text, ''), '({terms})')
ORDER BY warc_date DESC
LIMIT {limit};
"""


def cmd_owi_sql(a):
    cfg = load_cfg()
    sql = owi_sql(cfg, a.parquet, a.limit)
    if a.print or not a.parquet or a.parquet == "owi/**/*.parquet" and not Path("owi").exists():
        print(sql)
        print("-- get shards first (owilix, from openwebindex.eu; needs the free OWI account):")
        print("--   owilix remote list                         # collections and dates")
        print("--   owilix pull remote:<collection>/<date>  --path owi/   # or a narrower partition")
        print("--   owilix query local:owi/ 'select=url,title,domain_label' 'where=url_suffix=\\'at\\''")
        print("-- then: python3 scripts/wiki_lookup.py owi sql --parquet 'owi/**/*.parquet'")
        return
    try:
        import duckdb  # noqa: WPS433
    except ImportError:
        sys.exit("duckdb not installed: pip install duckdb, or use --print and run the SQL in owilix/duckdb")
    con = duckdb.connect()
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    for r in rows:
        print("\t".join(str(x) for x in r))
    print(f"\n{len(rows)} rows ({', '.join(cols)})", file=sys.stderr)
    if a.json:
        Path(a.json).write_text(json.dumps([dict(zip(cols, r)) for r in rows], indent=1, default=str))
        print(f"wrote {a.json}", file=sys.stderr)


def cmd_signatures(_a):
    for p in load_cfg()["signatures"]:
        print(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="fetch candidate wikis and grep for signatures")
    p.add_argument("--only", help="comma-separated substrings of candidate ids")
    p.add_argument("--url", action="append", help="probe this URL instead of the candidate list (repeatable)")
    p.add_argument("-e", "--expr", action="append", help="regex to use instead of the default signatures (repeatable)")
    p.add_argument("--context", type=int, default=5, help="sample snippets to keep per target")
    p.add_argument("-v", "--verbose", action="store_true", help="print sample snippets")
    p.add_argument("--json", help="write full results to this file")
    p.set_defaults(fn=cmd_probe)
    g = sub.add_parser("grep", help="regex over the forensic export revisions")
    g.add_argument("pattern")
    g.add_argument("--wikis", help="comma-separated export wikis (default: all eight)")
    g.add_argument("--field", default="name,label,change_summary,body", help="comma-separated revision fields to search")
    g.add_argument("-i", "--ignore-case", action="store_true")
    g.add_argument("--limit", type=int, default=50, help="rows to print (count is always complete)")
    g.set_defaults(fn=cmd_grep)
    s = sub.add_parser("signatures", help="print the default signature patterns")
    s.set_defaults(fn=cmd_signatures)
    o = sub.add_parser("owi", help="Open Web Index lookups (MOSAIC search API, owilix shards)")
    osub = o.add_subparsers(dest="owi_cmd", required=True)
    ot = osub.add_parser("terms", help="print the query set for a MOSAIC search box")
    ot.set_defaults(fn=cmd_owi_terms)
    os_ = osub.add_parser("search", help="run the query set through a MOSAIC /search endpoint")
    os_.add_argument("--base", default=OWI_DEFAULT_BASE, help="MOSAIC service base URL (default: public demo)")
    os_.add_argument("--path", default="/search", help="search endpoint path")
    os_.add_argument("--format", choices=["json", "xml"], default="json", help="response protocol to request")
    os_.add_argument("--only", choices=["all", "signatures", "hosts"], default="all")
    os_.add_argument("--term", action="append", help="query this term instead of the configured set (repeatable)")
    os_.add_argument("--size", type=int, default=50, help="results per query (passed as size=)")
    os_.add_argument("--timeout", type=int, default=45)
    os_.add_argument("--limit", type=int, default=10, help="kept rows to print per query")
    os_.add_argument("-v", "--verbose", action="store_true")
    os_.add_argument("--json", help="write full results to this file")
    os_.set_defaults(fn=cmd_owi_search)
    oq = osub.add_parser("sql", help="DuckDB scan of downloaded OWI parquet shards")
    oq.add_argument("--parquet", default="owi/**/*.parquet", help="glob of OWI shard files")
    oq.add_argument("--limit", type=int, default=500)
    oq.add_argument("--print", action="store_true", help="print the SQL and owilix recipe, do not run")
    oq.add_argument("--json", help="write rows to this file")
    oq.set_defaults(fn=cmd_owi_sql)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
