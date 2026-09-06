#!/usr/bin/env python3
"""Discover wiki installs by crawling outward from seed pages, fingerprint the
engine, and run the swarm-signature probe on each wiki's RecentChanges.

Read-only and polite: honours robots.txt, one request per host at a time with a
delay, bounded by --max-pages / --max-depth, resumable from --state.

  python3 scripts/wiki_crawler.py --max-pages 400 --max-depth 2
  python3 scripts/wiki_crawler.py --seed https://example.org/wiki.pl?SiteList --max-pages 100
  python3 scripts/wiki_crawler.py --report          # print the last run's table

Engines fingerprinted: UseModWiki, Oddmuse, ProWiki, PmWiki, MoinMoin, DokuWiki,
TiddlyWiki, MediaWiki (MediaWiki is recorded but not probed unless --mediawiki:
the big farms are not write-by-GET targets). Results go to
data/wiki_crawl_<date>.json; wikis with hits are printed with context.
"""
import argparse, collections, json, re, sys, socket, threading, time, urllib.parse, urllib.robotparser
socket.setdefaulttimeout(20)
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_lookup as W  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEDS = [
    "https://www.usemod.org/cgi-bin/wiki.pl?SiteList",
    "https://www.usemod.org/cgi-bin/wiki.pl?OtherWikis",
    "https://www.usemod.org/cgi-bin/wiki.pl?RecentVisitors",
    "http://meatballwiki.org/wiki/SwitchWiki",
    "http://meatballwiki.org/wiki/WikiEngines",
    "https://en.wikipedia.org/wiki/UseModWiki",
    "https://en.wikipedia.org/wiki/Oddmuse",
    "https://en.wikipedia.org/wiki/List_of_wiki_software",
    "https://prowiki.org/prowiki/wiki.cgi",
    "https://www.wikiservice.at/gruender/wiki.cgi?ProWiki",
    "https://www.dorfwiki.org/wiki.cgi?ProWiki",
]
SKIP_HOSTS = re.compile(r"(?:^|\.)(?:wikipedia|wikimedia|wiktionary|wikibooks|wikiquote|wikisource|wikiversity|wikivoyage|wikidata|fandom|github|gitlab|google|facebook|twitter|youtube|amazon|archive|doi|creativecommons|w3|gnu|sourceforge|apple|microsoft|mozilla|reddit|linkedin|x)\.(?:org|com|net|io)$", re.I)
WIKIISH = re.compile(r"wiki\.(?:pl|cgi)|/wiki/|/wiki\b|action=|RecentChanges|Recent_Changes|moin|pmwiki|doku|oddmuse|usemod", re.I)

ENGINES = [  # (name, body regex, rc builder)
    ("prowiki", re.compile(r"ProWiki|wiki\.cgi\?action=browse", re.I), lambda u: u + "?action=browse&id=RecentChanges&days=200&all=1"),
    ("usemod", re.compile(r"UseModWiki|UseMod Wiki|wiki\.pl\?action=", re.I), lambda u: u + "?action=rc&days=200&all=1&showedit=1"),
    ("oddmuse", re.compile(r"Oddmuse|action=rc;|action=browse;", re.I), lambda u: u + "?action=rc;days=200;all=1"),
    ("pmwiki", re.compile(r"pmwiki|PmWiki", re.I), lambda u: u + "?n=Site.AllRecentChanges"),
    ("moinmoin", re.compile(r"MoinMoin|moin_static|/RecentChanges\"", re.I), lambda u: u + "/RecentChanges"),
    ("dokuwiki", re.compile(r"DokuWiki|doku\.php", re.I), lambda u: u + "?do=recent"),
    ("tiddlywiki", re.compile(r"TiddlyWiki", re.I), lambda u: u),
    ("mediawiki", re.compile(r"MediaWiki|mw-body|wgPageName", re.I), lambda u: u + "/Special:RecentChanges?days=90&limit=500"),
]


def engine_of(body):
    for name, rx, _ in ENGINES:
        if rx.search(body):
            return name
    return None


def script_base(url):
    """Strip query and keep the CGI script path (wiki.pl / wiki.cgi) or the dir."""
    p = urllib.parse.urlsplit(url)
    m = re.match(r"(.*?\.(?:pl|cgi|php|py))(?:[/?].*)?$", p.path)
    path = m.group(1) if m else re.sub(r"/[^/]*$", "", p.path) or ""
    return f"{p.scheme}://{p.netloc}{path}"


def rc_url(engine, base):
    for name, _, build in ENGINES:
        if name == engine:
            return build(base)
    return base


class Crawler:
    def __init__(self, a):
        self.a = a
        self.sigs = W.compile_sigs(W.load_cfg()["signatures"])
        self.known = {urllib.parse.urlsplit(c["url"]).netloc.lower().replace("www.", "") for c in W.load_cfg()["candidates"]}
        self.state_path = Path(a.state)
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"seen": {}, "hosts": {}, "frontier": []}
        self.lock = threading.Lock()
        self.host_locks = collections.defaultdict(threading.Lock)
        self.host_last = collections.defaultdict(float)
        self.robots = {}

    def allowed(self, url):
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        rp = self.robots.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
            try:
                rp.read()
            except Exception:  # noqa: BLE001
                rp = None
            self.robots[root] = rp or False
        return True if not rp else rp.can_fetch("*", url)

    def polite_fetch(self, url):
        host = urllib.parse.urlsplit(url).netloc
        with self.host_locks[host]:
            wait = self.host_last[host] + self.a.delay - time.time()
            if wait > 0:
                time.sleep(wait)
            self.host_last[host] = time.time()
            return W.fetch(url, self.a.timeout)

    def visit(self, item):
        url, depth = item
        host = urllib.parse.urlsplit(url).netloc.lower().replace("www.", "")
        if not self.allowed(url):
            return url, depth, None, []
        code, body = self.polite_fetch(url)
        if code != 200 or len(body) < 200:
            return url, depth, None, []
        eng = engine_of(body)
        links = []
        if depth < self.a.max_depth:
            for href in re.findall(r'href=["\']([^"\'#>]+)', body):
                u = urllib.parse.urljoin(url, href.strip())
                if not u.startswith("http") or SKIP_HOSTS.search(urllib.parse.urlsplit(u).netloc):
                    continue
                if urllib.parse.urlsplit(u).netloc.lower().replace("www.", "") == host and not WIKIISH.search(u):
                    continue  # stay shallow on-site; only follow wiki-ish internal links
                links.append(u)
        return url, depth, eng, links

    def probe_host(self, host, eng, url):
        base = script_base(url)
        rc = rc_url(eng, base)
        r = W.probe_one(host, rc, self.sigs, 3)
        r["engine"] = eng
        r["found_via"] = url
        return r

    def run(self):
        st = self.state
        seeds = list(self.a.seed or DEFAULT_SEEDS)
        if self.a.seed_file:
            seeds += json.loads(Path(self.a.seed_file).read_text())
        frontier = st["frontier"] or [(s, 0) for s in seeds]
        pages = 0
        with ThreadPoolExecutor(self.a.workers) as ex:
            while frontier and pages < self.a.max_pages:
                hostcount = collections.Counter(urllib.parse.urlsplit(u).netloc for u in st["seen"])
                batch, rest = [], []
                for u, d in frontier:
                    h = urllib.parse.urlsplit(u).netloc
                    if len(batch) < self.a.workers * 2 and u not in st["seen"] and hostcount[h] < self.a.per_host:
                        batch.append((u, d)); hostcount[h] += 1
                    elif hostcount[h] < self.a.per_host and u not in st["seen"]:
                        rest.append((u, d))
                frontier = rest
                if not batch:
                    break
                for u, d in batch:
                    st["seen"][u] = d
                pages += len(batch)
                for url, depth, eng, links in ex.map(self.visit, batch):
                    host = urllib.parse.urlsplit(url).netloc.lower().replace("www.", "")
                    if eng and host not in st["hosts"] and host not in self.known:
                        if eng != "mediawiki" or self.a.mediawiki:
                            r = self.probe_host(host, eng, url)
                            st["hosts"][host] = r
                            flag = "HIT" if r["hits"] else ("BOT" if r["bot_check"] else "-")
                            print(f"{host:34s} {eng:10s} {r['http']:>3} {r['bytes']:>8}B {flag}", flush=True)
                            if r["hits"]:
                                for s in r["samples"]:
                                    print("      …", s[:200], flush=True)
                        else:
                            st["hosts"][host] = {"id": host, "engine": eng, "http": None, "hits": {}, "skipped": "mediawiki"}
                    frontier.extend((u, depth + 1) for u in links if u not in st["seen"])
                # wiki-ish links first, then dedupe
                seen = set()
                seen_hosts = {urllib.parse.urlsplit(u).netloc for u in st["seen"]}
                frontier = [x for x in sorted(frontier, key=lambda x: (0 if urllib.parse.urlsplit(x[0]).netloc not in seen_hosts else 1, 0 if WIKIISH.search(x[0]) else 1, x[1])) if not (x[0] in seen or seen.add(x[0]))]
                st["frontier"] = frontier
                self.state_path.write_text(json.dumps(st))
        self.report()

    def report(self):
        hosts = self.state["hosts"]
        by = collections.Counter(h.get("engine") for h in hosts.values())
        hits = [h for h in hosts.values() if h.get("hits")]
        print(f"\n{len(self.state['seen'])} pages seen, {len(hosts)} wiki hosts found {dict(by)}, {len(hits)} with signature hits, {sum(1 for h in hosts.values() if h.get('bot_check'))} bot checks, {len(self.state['frontier'])} URLs left in frontier")
        for h in hits:
            print(f"  HIT {h['id']} ({h['engine']}) {h['url']} -> {h['hits']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", action="append", help="seed URL (repeatable); default: usemod/meatball/prowiki directories")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--max-depth", type=int, default=2)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--per-host", type=int, default=12, help="max pages fetched per host")
    ap.add_argument("--seed-file", help="JSON list of extra seed URLs")
    ap.add_argument("--mediawiki", action="store_true", help="also probe MediaWiki installs")
    ap.add_argument("--state", default=str(ROOT / "data" / f"wiki_crawl_{date.today().isoformat()}.json"))
    ap.add_argument("--report", action="store_true", help="print the table from --state and exit")
    a = ap.parse_args()
    c = Crawler(a)
    if a.report:
        c.report()
    else:
        c.run()


if __name__ == "__main__":
    main()
