#!/usr/bin/env python3
"""Wayback CDX sweep for the wiki agent-swarm incident.

Closes the census gap named in analysis/wiki-census.md: live RecentChanges
windows on many candidate wikis are short or tarpitted, so May-June 2026 history
is unread. The Internet Archive's CDX index is a second, engine-agnostic record
of what those hosts served during the incident window.

For every candidate host (from data/wiki_candidates.json, plus a few extras named
by the census) this script:

  1. asks the CDX API for every 200 capture in the requested window
     (default 2026-05-12 .. 2026-07-15, the shortener start to the cleanup end);
  2. records the observed capture span and per-day counts, so "no captures" and
     "captures but nothing in them" stay distinguishable;
  3. runs the signature regexes over the captured URLs themselves (a page named
     ZZZAgentBridge is a trace even before its body is read);
  4. fetches the RecentChanges-style captures (raw, `id_` flag) and runs the
     signatures over their text, with the same blocked/tarpit guard as
     wiki_lookup.py.

Read-only. Nothing here writes to any wiki or to the Wayback Machine.

  python3 scripts/wayback_cdx_sweep.py --out data/wayback_cdx_sweep_2026-09-06.json
  python3 scripts/wayback_cdx_sweep.py --only ludism,oddmuse --no-fetch
  python3 scripts/wayback_cdx_sweep.py --host example.org --from 20260501 --to 20260801
  python3 scripts/wayback_cdx_sweep.py --report data/wayback_cdx_sweep_2026-09-06.json
  python3 scripts/wayback_cdx_sweep.py --refetch data/wayback_cdx_sweep_2026-09-06.json   # retry http0 reads
"""
import argparse, collections, json, re, sys, time, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_lookup import blocked_reason, compile_sigs, fetch, load_cfg, strip  # noqa: E402

CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/{ts}id_/{url}"
# hosts the census named but that have no entry in wiki_candidates.json
EXTRA_HOSTS = ["wikiservice.org", "wikiweb.at", "globalvillages.info",
               "kb5.zukunftslernorte.org", "campusosttirol.mustertheorie.de",
               "meatballwiki.org", "usemod.com"]
RC_URL = re.compile(r"action=rc|RecentChanges|do=recent|action=browse[^ ]*id=Recent", re.I)
NOISE = re.compile(r"\.(?:gif|png|jpe?g|ico|css|js|svg|woff2?)(?:$|\?)", re.I)


def host_of(url):
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def cdx_query(host, start, end, limit, retries=5):
    q = {"url": f"{host}/*", "from": start, "to": end, "output": "json",
         "fl": "timestamp,original,statuscode,digest", "filter": "statuscode:200", "limit": limit}
    url = f"{CDX}?{urllib.parse.urlencode(q)}"
    for i in range(retries):
        code, body = fetch(url, timeout=120)
        if code == 200:
            try:
                rows = json.loads(body) if body.strip() else []
            except json.JSONDecodeError:
                return code, None, "bad-json"
            return code, [dict(zip(rows[0], r)) for r in rows[1:]] if rows else [], None
        if code in (429, 502, 503, 504, 0):
            time.sleep(10 * (i + 1))
            continue
        return code, None, f"http{code}"
    return code, None, f"http{code}-after-retries"


def sweep_host(host, sigs, start, end, limit, fetch_rc, max_fetch, ctx=4):
    code, rows, err = cdx_query(host, start, end, limit)
    res = {"host": host, "requested": {"from": start, "to": end, "limit": limit},
           "cdx_http": code, "cdx_error": err, "captures": 0, "captures_nonasset": 0,
           "observed": None, "per_day": {}, "rc_captures": 0, "url_hits": {}, "url_samples": [],
           "fetched": [], "outcome": None}
    if rows is None:
        res["outcome"] = "cdx_unavailable"
        return res
    rows = [r for r in rows if not NOISE.search(r["original"])]
    res["captures"] = len(rows)
    res["captures_nonasset"] = len(rows)
    if not rows:
        res["outcome"] = "no_captures_in_window"
        return res
    ts = sorted(r["timestamp"] for r in rows)
    res["observed"] = {"first": ts[0], "last": ts[-1]}
    res["per_day"] = dict(sorted(collections.Counter(t[:8] for t in ts).items()))
    res["truncated"] = len(rows) >= limit
    hits = collections.Counter()
    for r in rows:
        u = urllib.parse.unquote(r["original"])
        for pat, rx in sigs:
            if rx.search(u):
                hits[pat] += 1
                if len(res["url_samples"]) < 25:
                    res["url_samples"].append({"ts": r["timestamp"], "url": r["original"]})
    res["url_hits"] = dict(hits.most_common())
    rc = [r for r in rows if RC_URL.search(r["original"])]
    # dedupe by digest: identical RC snapshots need only one read
    seen, rc_unique = set(), []
    for r in rc:
        if r["digest"] not in seen:
            seen.add(r["digest"])
            rc_unique.append(r)
    res["rc_captures"] = len(rc)
    res["rc_unique"] = len(rc_unique)
    if fetch_rc:
        for r in rc_unique[:max_fetch]:
            code, body = fetch(RAW.format(ts=r["timestamp"], url=r["original"]), timeout=90)
            text = strip(body)
            bh = collections.Counter()
            samples = []
            for pat, rx in sigs:
                for m in rx.finditer(text):
                    bh[pat] += 1
                    if len(samples) < ctx:
                        a, b = max(0, m.start() - 80), min(len(text), m.end() + 80)
                        samples.append(text[a:b].strip())
            res["fetched"].append({"ts": r["timestamp"], "url": r["original"], "http": code,
                                   "bytes": len(body), "blocked": blocked_reason(code, text, r["original"]),
                                   "hits": dict(bh.most_common()), "samples": samples})
            time.sleep(1)
    any_body = any(f["hits"] for f in res["fetched"])
    res["outcome"] = ("signature_hit" if (hits or any_body)
                      else "captures_no_signature" if (res["fetched"] or not fetch_rc)
                      else "captures_no_rc")
    return res


def refetch(path, sigs, ctx=4):
    """Retry archived-page reads that failed with a connection error (http0).

    The Archive's web front end drops connections after a burst of raw reads;
    the first pass leaves those rows as blocked="http0". Re-read them with
    backoff and update the saved file in place, preserving the first-pass rows.
    """
    data = json.loads(Path(path).read_text())
    todo = [(r, f) for r in data["results"] for f in r["fetched"] if f["blocked"] == "http0"]
    print(f"[refetch] {len(todo)} failed reads", file=sys.stderr)
    for r, f in todo:
        for i in range(4):
            code, body = fetch(RAW.format(ts=f["ts"], url=f["url"]), timeout=90)
            if code:
                break
            time.sleep(15 * (i + 1))
        text = strip(body)
        bh, samples = collections.Counter(), []
        for pat, rx in sigs:
            for m in rx.finditer(text):
                bh[pat] += 1
                if len(samples) < ctx:
                    a, b = max(0, m.start() - 80), min(len(text), m.end() + 80)
                    samples.append(text[a:b].strip())
        f.update({"http": code, "bytes": len(body), "blocked": blocked_reason(code, text, f["url"]),
                  "hits": dict(bh.most_common()), "samples": samples, "refetched": True})
        print(f"  {r['host']} {f['ts']} -> {f['blocked'] or 'ok'} hits={sum(bh.values())}", file=sys.stderr, flush=True)
        time.sleep(3)
    for r in data["results"]:
        if r["outcome"] in ("captures_no_signature", "signature_hit"):
            r["outcome"] = "signature_hit" if (r["url_hits"] or any(f["hits"] for f in r["fetched"])) else "captures_no_signature"
    data["refetch_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    Path(path).write_text(json.dumps(data, indent=1))
    return data["results"]


def report(results):
    order = {"signature_hit": 0, "captures_no_rc": 1, "captures_no_signature": 2,
             "no_captures_in_window": 3, "cdx_unavailable": 4}
    print(f"{'host':40} {'caps':>6} {'rc':>4} {'read':>4} {'urlhit':>6} {'bodyhit':>7}  observed span         outcome")
    for r in sorted(results, key=lambda r: (order.get(r["outcome"], 9), -r["captures"])):
        span = f"{r['observed']['first'][:8]}..{r['observed']['last'][:8]}" if r["observed"] else "-"
        bodyhit = sum(sum(f["hits"].values()) for f in r["fetched"])
        print(f"{r['host']:40} {r['captures']:>6} {r['rc_captures']:>4} {len(r['fetched']):>4} "
              f"{sum(r['url_hits'].values()):>6} {bodyhit:>7}  {span:20}  {r['outcome']}"
              + (f" ({r['cdx_error']})" if r["cdx_error"] else "") + (" [truncated]" if r.get("truncated") else ""))
    for r in results:
        if r["outcome"] == "signature_hit":
            print(f"\n== {r['host']}: url_hits={r['url_hits']}")
            for s in r["url_samples"][:10]:
                print(f"   {s['ts']} {s['url']}")
            for f in r["fetched"]:
                if f["hits"]:
                    print(f"   body {f['ts']} {f['url']} hits={f['hits']}")
                    for s in f["samples"]:
                        print(f"      … {s} …")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", default="20260512")
    ap.add_argument("--to", dest="end", default="20260715")
    ap.add_argument("--limit", type=int, default=20000, help="max CDX rows per host")
    ap.add_argument("--only", help="comma-separated substrings of candidate ids/hosts")
    ap.add_argument("--host", action="append", help="sweep this host instead of the candidate list")
    ap.add_argument("--no-extras", action="store_true", help="skip the census-named extra hosts")
    ap.add_argument("--no-fetch", action="store_true", help="index only; do not read captured pages")
    ap.add_argument("--max-fetch", type=int, default=40, help="RC captures to read per host")
    ap.add_argument("-e", "--expr", action="append", help="extra signature regex")
    ap.add_argument("--out", help="write JSON results here")
    ap.add_argument("--report", help="print a report from a saved JSON file and exit")
    ap.add_argument("--refetch", help="retry the http0 page reads in a saved JSON file, update it, and report")
    a = ap.parse_args()
    if a.report:
        report(json.loads(Path(a.report).read_text())["results"])
        return
    cfg = load_cfg()
    sigs = compile_sigs(cfg["signatures"] + (a.expr or []))
    if a.refetch:
        report(refetch(a.refetch, sigs))
        return
    if a.host:
        hosts = a.host
    else:
        hosts = list(dict.fromkeys(host_of(c["url"]) for c in cfg["candidates"]))
        if not a.no_extras:
            hosts += [h for h in EXTRA_HOSTS if h not in hosts]
        if a.only:
            keys = a.only.split(",")
            ids = {host_of(c["url"]) for c in cfg["candidates"] if any(k in c["id"] for k in keys)}
            hosts = [h for h in hosts if h in ids or any(k in h for k in keys)]
    results = []
    for h in hosts:
        print(f"[cdx] {h} …", file=sys.stderr, flush=True)
        r = sweep_host(h, sigs, a.start, a.end, a.limit, not a.no_fetch, a.max_fetch)
        print(f"      {r['captures']} captures, {r['rc_captures']} rc, outcome={r['outcome']}", file=sys.stderr, flush=True)
        results.append(r)
        time.sleep(2)
    out = {"run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "window": {"from": a.start, "to": a.end},
           "signatures": cfg["signatures"] + (a.expr or []), "note": "CDX 200 captures only; asset URLs dropped; "
           "rc pages read via id_ raw captures; a zero is absence of a signature in what the Archive holds, not proof of no edits.",
           "results": results}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print(f"wrote {a.out}", file=sys.stderr)
    report(results)


if __name__ == "__main__":
    main()
