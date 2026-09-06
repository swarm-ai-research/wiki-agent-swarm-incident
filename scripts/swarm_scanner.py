#!/usr/bin/env python3
"""Generic scan for agent-swarm activity on open-edit wikis.

Unlike wiki_lookup.py (which looks for THIS swarm's fingerprints), this scores a
wiki's RecentChanges for swarm-like behaviour of any origin:

  cloud     editors resolving to cloud/VPS reverse DNS (azure, ec2, ovh, hetzner…)
  handles   CamelCase handles ending in Agent/Bot/Helper/Researcher/Fleet/Relay…
  words     automation vocabulary in summaries (agent, fleet, swarm, payload,
            round N, deadline, editability test, LLM/GPT/Claude/OpenAI…)
  payload   base64 runs and JSON envelopes in visible text
  infra     counters, webhooks, fetch proxies, pastes, shorteners in URLs
  burst     max edits/day in 2025-26 vs the median active day

Input is a JSON list of {name, engine, urls} (WikiIndex export shape) or --url
targets. Output: ranked JSON with per-signal counts and evidence snippets, and a
table of the top hits. Read-only, one request per wiki.

  python3 scripts/swarm_scanner.py --input wikiindex_openedit.json --skip-engines Wikia,Fandom --out data/swarm_scan.json
  python3 scripts/swarm_scanner.py --url https://example.org/wiki.pl?action=rc
  python3 scripts/swarm_scanner.py --report data/swarm_scan.json --top 30
"""
import argparse, collections, json, re, statistics, sys, urllib.parse
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_lookup as W  # noqa: E402

SIG = {
    "cloud": re.compile(r"azure|amazonaws|ec2-\d|compute\.internal|googleusercontent|\.ovh\.|ip-\d+-\d+-\d+|hetzner|digitalocean|linode|vultr|contabo|scaleway|\b(?:20|40|52|104|172\.1[6-9]|172\.2\d|172\.3[01])\.\d+\.\d+\.\d+\b", re.I),
    "handles": re.compile(r"\b(?:[A-Z][a-z]+){1,3}(?:Agent|Helper|Researcher|Worker|Fleet|Swarm|Relay|Probe|Bridge|Scout|Runner|Tester)[A-Za-z0-9]*\b"),
    "bots": re.compile(r"\b(?:[A-Z][a-z]+){1,3}Bot[A-Za-z0-9]*\b"),
    "words": re.compile(r"\b(?:automated|autonomous|agents?|fleet|swarm|relay|envelope|payload|checkpoint|round \d+|deadline|task \d+|sandbox test|editability test|LLM|GPT-?\d|Claude|OpenAI|Anthropic|Gemini)\b", re.I),
    "payload": re.compile(r"[A-Za-z0-9+/]{120,}={0,2}|\{\s*\"v\"\s*:\s*\d|\"payload\"\s*:"),
    "infra": re.compile(r"counterapi|countapi|ntfy\.sh|webhook\.site|r\.jina\.ai|markdown\.new|allorigins|corsproxy|cors\.[a-z]+\.workers|jqp\.vercel|pastebin|paste\.|is\.gd|tinyurl|v\.gd|da\.gd|md\.succ", re.I),
}
# Any-year date headers, so old rows segment under their OWN (old) header instead
# of leaking into a recent one; only 2025–26 buckets are counted (RECENT_YEARS).
_M = "January|February|March|April|May|June|July|August|September|October|November|December"
_DM = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
DATES = [
    re.compile(r"\b((?:19|20)\d\d)-(\d\d)-(\d\d)\b"),
    re.compile(r"\b(?:" + _M + r") \d{1,2},? (?:19|20)\d\d\b"),
    re.compile(r"\b\d{1,2}\. (?:" + _DM + r") (?:19|20)\d\d\b"),
    re.compile(r"\b\d{1,2}\.\d{1,2}\.((?:19|20)\d\d)\b"),
    re.compile(r"\b\d{1,2} (?:" + _M + r") (?:19|20)\d\d\b"),
]
TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
_YEAR = re.compile(r"(?:19|20)\d\d")
RECENT_YEARS = {2025, 2026}
TODAY = date.today().isoformat()


def _countable(daykey):
    """A day header worth counting: year in 2025–26, and (for ISO keys) not in
    the future — a `2026-10-10` on a page today is a version string, not a day."""
    y = _YEAR.search(daykey)
    if not y or int(y.group(0)) not in RECENT_YEARS:
        return False
    iso = re.match(r"((?:19|20)\d\d-\d\d-\d\d)", daykey)
    return not (iso and iso.group(1) > TODAY)
WEIGHTS = {"cloud": 2, "handles": 3, "bots": 0.2, "words": 1.5, "payload": 4, "infra": 2}
CAPS = {"cloud": 10, "handles": 10, "bots": 10, "words": 10, "payload": 5, "infra": 10}
ENGINE_RC = {
    "mediawiki": "/Special:RecentChanges?days=30&limit=500",
    "usemod": "?action=rc&days=200&all=1",
    "oddmuse": "?action=rc;days=200;all=1",
    "prowiki": "?action=browse&id=RecentChanges&days=200&all=1",
    "pmwiki": "?n=Site.AllRecentChanges",
    "moinmoin": "/RecentChanges",
    "dokuwiki": "?do=recent",
    "phpwiki": "/RecentChanges",
}


def engine_key(e):
    e = (e or "").lower()
    for k in ("usemod", "oddmuse", "prowiki", "pmwiki", "moinmoin", "dokuwiki", "phpwiki", "mediawiki"):
        if k in e.replace(" ", ""):
            return k
    return "mediawiki" if "wiki" in e else "unknown"


def rc_from(engine, url):
    p = urllib.parse.urlsplit(url)
    if engine == "mediawiki":
        # Statistics URL is usually .../index.php?title=Special:Statistics or /wiki/Special:Statistics
        if "title=" in p.query:
            return f"{p.scheme}://{p.netloc}{p.path}?title=Special:RecentChanges&days=30&limit=500"
        path = re.sub(r"/Special:.*$", "", p.path)
        return f"{p.scheme}://{p.netloc}{path}{ENGINE_RC['mediawiki']}"
    m = re.match(r"(.*?\.(?:pl|cgi|php|py))(?:[/?].*)?$", p.path)
    if m:
        return f"{p.scheme}://{p.netloc}{m.group(1)}{ENGINE_RC.get(engine, '?action=rc')}"
    path = re.sub(r"/[^/]*$", "", p.path)
    return f"{p.scheme}://{p.netloc}{path}{ENGINE_RC.get(engine, '/RecentChanges')}"


def day_counts(text):
    """Per-day edit-row counts, robust to both RecentChanges layouts.

    Older versions counted date-token occurrences, which equals rows only when
    each row carries its own date (inline, as some Oddmuse skins do). MediaWiki
    and UseMod print the date once as a day header and put a time on each row, so
    token-counting saw ~1 per day and the burst signal went blind. Here every
    HH:MM row is bucketed under its preceding day header, so burst works for all.
    """
    import bisect
    headers = sorted((m.start(), m.group(0)) for rx in DATES for m in rx.finditer(text))
    times = [m.start() for m in TIME.finditer(text)]
    if headers and len(times) > len(headers):
        hpos = [h[0] for h in headers]
        counts = collections.Counter()
        for t in times:
            i = bisect.bisect_right(hpos, t) - 1
            if i >= 0 and _countable(headers[i][1]):   # old-year buckets drop out here
                counts[headers[i][1]] += 1
        if counts:
            return counts
    return collections.Counter(d for _, d in headers if _countable(d))


def score_text(text):
    sig = {k: len(rx.findall(text)) for k, rx in SIG.items()}
    days = day_counts(text)
    vals = sorted(days.values())
    burst = (vals[-1] / max(1.0, statistics.median(vals))) if vals else 0.0
    score = sum(WEIGHTS[k] * min(sig[k], CAPS[k]) for k in WEIGHTS) + min(burst, 20)
    ev = []
    for k, rx in SIG.items():
        for m in list(rx.finditer(text))[:2]:
            a, b = max(0, m.start() - 70), min(len(text), m.end() + 70)
            ev.append(f"[{k}] …{text[a:b].strip()}…")
    return {"score": round(score, 1), "signals": sig, "burst": round(burst, 1), "days_2526": len(days),
            "max_day": (max(days.items(), key=lambda x: x[1]) if days else None), "evidence": ev[:8]}


def scan_one(t):
    name, engine, url = t["name"], t["engine"], t["url"]
    code, body = W.fetch(url, 20)
    r = {"name": name, "engine": engine, "url": url, "http": code, "bytes": len(body)}
    if code != 200 or len(body) < 300:
        r.update(score=0, signals={}, evidence=[])
        return r
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    text = W.strip(body)
    r["blocked"] = W.blocked_reason(code, text, url)
    r["bot_check"] = r["blocked"] in ("http402", "botcheck")
    r.update(score_text(text))
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="JSON list of {name, engine, urls}")
    ap.add_argument("--url", action="append", help="scan this RecentChanges URL directly (repeatable)")
    ap.add_argument("--skip-engines", default="Wikia,Fandom", help="comma list of engine names to skip")
    ap.add_argument("--statuses", default="Active,Vibrant,New,Needs love,", help="WikiIndex statuses to include")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="max wikis to scan (0 = all)")
    ap.add_argument("--out", default="swarm_scan.json")
    ap.add_argument("--report", help="print the table from this results file and exit")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    if a.report:
        report(json.loads(Path(a.report).read_text()), a.top)
        return
    targets = []
    if a.url:
        targets = [{"name": u, "engine": "unknown", "url": u} for u in a.url]
    else:
        skip = {s.strip().lower() for s in a.skip_engines.split(",") if s.strip()}
        st = {s.strip() for s in a.statuses.split(",")}
        seen = set()
        for e in json.loads(Path(a.input).read_text()):
            if (e.get("engine") or "").lower() in skip or e.get("status", "") not in st or not e.get("urls"):
                continue
            eng = engine_key(e.get("engine"))
            url = rc_from(eng, e["urls"][0])
            host = urllib.parse.urlsplit(url).netloc
            if host in seen:
                continue
            seen.add(host)
            targets.append({"name": e["name"], "engine": eng, "url": url})
    if a.limit:
        targets = targets[: a.limit]
    print(f"scanning {len(targets)} wikis", file=sys.stderr)
    results = []
    with ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(scan_one, targets), 1):
            results.append(r)
            if i % 100 == 0:
                print(f"  {i}/{len(targets)}", file=sys.stderr)
                Path(a.out).write_text(json.dumps(results, indent=0))
    results.sort(key=lambda r: -r.get("score", 0))
    Path(a.out).write_text(json.dumps(results, indent=0))
    report(results, a.top)


def report(results, top):
    live = [r for r in results if r.get("http") == 200 and r.get("bytes", 0) > 300]
    blocked = [r for r in live if r.get("blocked")]
    print(f"{len(results)} scanned, {len(live)} live, {len(blocked)} blocked/tarpit "
          f"({', '.join(sorted({r['blocked'] for r in blocked})) or 'none'}) — a blocked page is not a clean read")
    print(f"{'score':>5} {'name':30s} {'engine':9s} {'burst':>5} {'days':>4}  signals")
    for r in sorted(live, key=lambda r: -r.get("score", 0))[:top]:
        print(f"{r['score']:5.1f} {r['name'][:30]:30s} {r['engine']:9s} {r.get('burst',0):5.1f} {r.get('days_2526',0):4d}  {r.get('signals')}")
    print("\nevidence for top 8:")
    for r in sorted(live, key=lambda r: -r.get("score", 0))[:8]:
        print(f"-- {r['name']}  {r['url']}")
        for e in r.get("evidence", [])[:5]:
            print("    ", e[:220])


if __name__ == "__main__":
    main()
