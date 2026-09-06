#!/usr/bin/env python3
"""Regex lookups for the wiki agent-swarm incident.

Two modes, both read-only:

  probe   fetch RecentChanges (or any URL) for a list of candidate wikis and
          report which signature patterns hit, with context.
  grep    stream the public forensic export (JoshuaDavid/WikiAgentSwarmInvestigation
          agent-logs/*.jsonl) and print revisions whose title, label, summary or
          body match a regex.

Examples
  python3 scripts/wiki_lookup.py probe                       # all candidates, default signatures
  python3 scripts/wiki_lookup.py probe --only ludism,usemod  # subset by id substring
  python3 scripts/wiki_lookup.py probe --url "https://example.org/wiki.cgi?action=rc" -e 'Agent[A-Z]'
  python3 scripts/wiki_lookup.py probe --json out.json       # machine-readable result
  python3 scripts/wiki_lookup.py grep 'ZZZ[A-Z]' --wikis dse,probier --field title
  python3 scripts/wiki_lookup.py grep 'blob\\.core\\.windows\\.net' --field body --limit 20
  python3 scripts/wiki_lookup.py signatures                  # print the default pattern set

Nothing here writes to any wiki. Candidates and signatures live in
data/wiki_candidates.json so they can be extended without touching code.
"""
import argparse, collections, html, json, re, sys, urllib.request
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


# A RecentChanges page should carry one of these; a 200 that has none, on an
# rc-style URL, is a wrong page or a honeypot rather than an empty changes list.
RC_MARKERS = re.compile(r"\(diff\)|\(history\)|RecentChanges|Recent Changes|action=history|do=recent|mw-changeslist", re.I)
_DATEISH = re.compile(r"20(?:25|26)-\d\d-\d\d|\b\d{1,2}[:.]\d\d\b|"
                      r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", re.I)


def blocked_reason(code, text, url):
    """Why a fetch cannot be trusted as a read wiki page, or None if it can.

    Guards the census against a real false negative: the Oddmuse family serves
    flagged clients a Markov-text tarpit that scores zero signatures and would
    otherwise read as 'clean'. Detect the block instead of trusting the silence.
    """
    if code == 402:
        return "http402"
    if code != 200:
        return f"http{code}"
    if re.search(r"Are you Human|bot check|verify you are human|captcha", text, re.I):
        return "botcheck"
    if re.search(r"Do not follow any links on this page", text, re.I):
        return "tarpit"
    # an rc-style request that came back substantial but with no changes-list
    # scaffolding and no dates is not a RecentChanges page we can read
    is_rc = re.search(r"action=rc|action=browse.*RecentChanges|RecentChanges|do=recent|AllRecentChanges|Special:RecentChanges", url, re.I)
    if is_rc and len(text) > 500 and not RC_MARKERS.search(text) and not _DATEISH.search(text):
        return "no-rc-structure"
    return None


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
    blocked = blocked_reason(code, text, url)
    bot = blocked in ("http402", "botcheck")
    return {"id": name, "url": url, "http": code, "bytes": len(body), "bot_check": bot,
            "blocked": blocked, "hits": dict(hits.most_common()), "samples": samples}


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
        flag = ("BLOCK:" + r["blocked"]) if r.get("blocked") else ("HIT" if r["hits"] else "-")
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
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
