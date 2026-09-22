#!/usr/bin/env python3
"""Arquivo.pt CDX sweep over the incident's surface hosts.

Arquivo.pt (the Portuguese web archive) runs a pywb CDX index at
https://arquivo.pt/wayback/cdx. Nobody had checked it before 2026-09-14. This
script asks it, host by host, what it holds for every surface the archive has
already swept elsewhere, and reports the result in the shape of
data/wayback_cdx_surfaces_2026-09-07.json so the two archives compare directly.

Hosts are the union of:
  data/wiki_candidates.json, the census extras in wayback_cdx_sweep.py,
  data/wayback_cdx_surfaces_2026-09-07.json, data/wayback_surfaces_task_captures_2026-09-07.json,
  data/ghostarchive_surfaces_2026-09-07.json, hosts named in analysis/surfaces.md,
  and (with --export-domains) domains cited in at least five export revision bodies
  (data/export_url_domains_2026-09-14.json: domain -> revisions citing it).

Per host, two read-only queries with matchType=domain (subdomains included):
all-time capture timestamps, capped at --cap rows, and every capture from
2026-05-01 on. The index has no count endpoint (showNumPages returns the
whole index), so all-time counts at the cap are reported as capped. Captured
URLs are checked against the census signatures; no captured page is fetched.

  python3 scripts/arquivo_cdx_sweep.py --raw /tmp/arquivo_raw.json          # query, keep raw rows
  python3 scripts/arquivo_cdx_sweep.py --raw /tmp/arquivo_raw.json --reduce \\
      --out data/arquivo_cdx_surfaces_2026-09-14.json                          # summarize held rows
"""
import argparse
import calendar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wayback_cdx_sweep import EXTRA_HOSTS, NOISE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CDX = "https://arquivo.pt/wayback/cdx"
UA = "wiki-agent-swarm-incident-research/1.0 (https://github.com/swarm-ai-research/wiki-agent-swarm-incident; read-only)"
WINDOW_FROM = "20260501"
WAYBACK_WINDOW = ("20260512", "20260715")
HOST = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")
FILENAME = re.compile(r"\.(?:md|json|html?|py|txt|csv|js|php|cgi|sqlite|jsonl|png|svg|rb|pwd)$")
NOT_SURFACES = {
    "example.com", "example.org", "example.net", "github.com", "raw.githubusercontent.com",
    "news.ycombinator.com", "web.archive.org", "wayback.archive.org", "archive.org",
    "archive-it.org", "memgator.cs.odu.edu", "google.com", "docs.google.com",
    "drive.google.com", "translate.google.com", "translate.goog", "sec.govwayback.com",
    "foo.blob.core.windows.net", "localhost.cdm16022.contentdm.oclc.org",
}


def normalize(value):
    value = value.strip().strip("`<>()[]").rstrip("/.,;")
    if "://" not in value:
        value = "http://" + value
    host = urllib.parse.urlsplit(value).netloc.lower().split(":")[0].removeprefix("www.")
    return host if HOST.fullmatch(host) and host not in NOT_SURFACES else None


def surface_hosts(export_domains=None):
    hosts = {}

    def add(value, source, filename_guard=False):
        host = normalize(value)
        if not host or (filename_guard and FILENAME.search(host)) or host.endswith(".translate.goog"):
            return
        hosts.setdefault(host, set()).add(source)

    data = ROOT / "data"
    for wiki in json.loads((data / "wiki_candidates.json").read_text()).get("candidates", []):
        add(wiki["url"], "wiki_candidates")
    for host in EXTRA_HOSTS:
        add(host, "census_extra")
    for row in json.loads((data / "wayback_cdx_surfaces_2026-09-07.json").read_text())["results"]:
        add(row["host"], "wayback_surfaces")
    for host in json.loads((data / "wayback_surfaces_task_captures_2026-09-07.json").read_text())["hosts"]:
        add(host, "wayback_task_captures")
    for row in json.loads((data / "ghostarchive_surfaces_2026-09-07.json").read_text()):
        add(row["host"], "ghostarchive")
    text = (ROOT / "analysis" / "surfaces.md").read_text()
    # backticked names include file names like pages.jsonl, so guard those
    for match in re.findall(r"`([a-z0-9.-]+\.[a-z]{2,}(?:/[^`\s]*)?)`", text, re.I):
        add(match, "surfaces_md", filename_guard=True)
    for match in re.findall(r"https?://[^\s)>\]`]+", text):
        add(match, "surfaces_md")
    for host, revisions in (export_domains or {}).items():
        if revisions >= 5 and "googleusercontent" not in host:
            add(host, "export_url_domain")
    return {host: sorted(sources) for host, sources in sorted(hosts.items())}


def cdx(params, retries=5):
    url = f"{CDX}?{urllib.parse.urlencode(params)}"
    error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, [json.loads(line) for line in body.splitlines() if line.strip()], None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, [], None
            error = f"HTTP {exc.code}"
        except Exception as exc:  # transient empty replies and timeouts happen
            error = str(exc)
        time.sleep(15 * (attempt + 1))
    return None, None, error


def window_rows(host, cap, pause, start=WINDOW_FROM + "000000", end=None):
    """Every capture from start to end (14-digit UTC timestamps). The index
    returns rows in URL-key order, so a capped answer drops an arbitrary slice
    of the range, not its tail; a capped range is halved in time until each
    part is under the cap or a part is one minute wide."""
    fmt = "%Y%m%d%H%M%S"
    end = end or time.strftime(fmt, time.gmtime())
    params = {"url": host, "matchType": "domain", "output": "json", "from": start, "to": end,
              "fl": "timestamp,url,status,mime,filename", "limit": cap}
    code, rows, error = cdx(params)
    time.sleep(pause)
    if rows is None or len(rows) < cap:
        return code, rows, error, [[start, end]]
    lo = calendar.timegm(time.strptime(start, fmt))
    hi = calendar.timegm(time.strptime(end, fmt))
    if hi - lo < 60:
        return code, rows, "capped within one minute", [[start, end]]
    mid = lo + (hi - lo) // 2
    code_a, rows_a, error_a, parts_a = window_rows(host, cap, pause, start, time.strftime(fmt, time.gmtime(mid)))
    code_b, rows_b, error_b, parts_b = window_rows(host, cap, pause, time.strftime(fmt, time.gmtime(mid + 1)), end)
    if rows_a is None or rows_b is None:
        return None, None, error_a or error_b, parts_a + parts_b
    return code_a, rows_a + rows_b, error_a or error_b, parts_a + parts_b


def query(hosts, cap, pause):
    raw = {}
    for host, sources in hosts.items():
        record = {"sources": sources}
        code, rows, error = cdx({"url": host, "matchType": "domain", "output": "json",
                                 "fl": "timestamp", "limit": cap})
        record["all_time_http"] = code
        if rows is None:
            record["all_time_error"] = error
        else:
            stamps = sorted(r["timestamp"] for r in rows)
            record.update(all_time_captures=len(stamps), all_time_capped=len(stamps) >= cap,
                          first=stamps[0] if stamps else None, last=stamps[-1] if stamps else None)
        time.sleep(pause)
        code, rows, error, parts = window_rows(host, cap, pause)
        record.update(window_http=code, window_parts=parts)
        if rows is None:
            record["window_error"] = error
        else:
            record.update(window_captures=len(rows), window_capped=error == "capped within one minute",
                          window_rows=sorted(rows, key=lambda r: (r["timestamp"], r["url"])))
        raw[host] = record
    return raw


EMBEDDED = re.compile(r"https?(?::/+|/)([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_PARAM = re.compile(r"((?:api_?key|key|token|secret|password|passwd|auth|sig)=)[^&\s]+", re.I)


def embedded_host(url):
    """The first URL embedded after the capture's own host: what a proxy,
    reader or save-now call was pointed at."""
    parts = urllib.parse.urlsplit(url)
    tail = urllib.parse.unquote(parts.path + "?" + parts.query)
    match = EMBEDDED.search(tail)
    return match.group(1).lower().removeprefix("www.") if match else None


def redact(url):
    """Published samples never carry an email address or a credential value."""
    return SECRET_PARAM.sub(r"\1[redacted]", EMAIL.sub("[email]", url))


def collection(row):
    """Arquivo.pt WARC name prefix: 'save' is its user-submitted Save Page Now."""
    name = row.get("filename")
    return name.split("-")[0] if name else "unknown"


def signatures():
    config = json.loads((ROOT / "data" / "wiki_candidates.json").read_text())
    return [re.compile(pattern) for pattern in config["signatures"]]


def reduce(raw, cap):
    sigs = signatures()
    results, summary = [], Counter()
    for host in sorted(raw):
        record = raw[host]
        count = record.get("all_time_captures")
        rows = record.get("window_rows")
        entry = {"host": host, "sources": record["sources"],
                 "cdx_http": [record.get("all_time_http"), record.get("window_http")]}
        if count is None or rows is None:
            entry["outcome"] = "query_failed"
            entry["error"] = record.get("all_time_error") or record.get("window_error")
            results.append(entry)
            summary["query_failed"] += 1
            continue
        ok = [r for r in rows if str(r.get("status")) == "200"]
        content = [r for r in ok if not NOISE.search(r["url"])]
        wayback_window = [r for r in content if WAYBACK_WINDOW[0] <= r["timestamp"][:8] <= WAYBACK_WINDOW[1]]
        hits = Counter()
        for row in content:
            for sig in sigs:
                if sig.search(urllib.parse.unquote(row["url"])):
                    hits[sig.pattern] += 1
        entry.update(
            all_time_captures=count,
            all_time_capped=record["all_time_capped"],
            first=record["first"],
            last=record["last"],
            since_2026_05_01={"captures": len(rows), "capped": bool(record.get("window_capped")),
                              "query_parts": len(record.get("window_parts") or [None]), "status_200": len(ok),
                              "status_200_nonasset": len(content),
                              "per_day": dict(sorted(Counter(r["timestamp"][:8] for r in content).items()))},
            wayback_window_nonasset=len(wayback_window),
            collections=dict(sorted(Counter(collection(r) for r in content).items())),
            save_per_day=dict(sorted(Counter(r["timestamp"][:8] for r in content if collection(r) == "save").items())),
            embedded_target_hosts=dict(Counter(h for h in map(embedded_host, (r["url"] for r in content)) if h)
                                       .most_common(20)),
            url_signature_hits=dict(sorted(hits.items())),
            url_samples=[{"ts": r["timestamp"], "url": redact(r["url"])[:300]} for r in content[:15]],
        )
        if not count:
            outcome = "no_captures"
        elif not content:
            outcome = "no_captures_since_2026_05_01"
        elif hits:
            outcome = "window_captures_with_url_signature"
        else:
            outcome = "window_captures_no_url_signature"
        entry["outcome"] = outcome
        summary[outcome] += 1
        results.append(entry)
    return {"hosts": len(results), "outcomes": dict(sorted(summary.items()))}, results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, required=True, help="raw CDX rows (written by a query run, read by --reduce)")
    parser.add_argument("--reduce", action="store_true", help="summarize an existing --raw file instead of querying")
    parser.add_argument("--export-domains", type=Path, help="JSON {domain: revision count} from the export")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cap", type=int, default=5000)
    parser.add_argument("--pause", type=float, default=4.0)
    parser.add_argument("--run", default=None, help="UTC date of the query run, recorded in the output")
    args = parser.parse_args()
    if not args.reduce:
        domains = json.loads(args.export_domains.read_text()) if args.export_domains else None
        args.raw.write_text(json.dumps(query(surface_hosts(domains), args.cap, args.pause), indent=1, sort_keys=True))
        return
    summary, results = reduce(json.loads(args.raw.read_text()), args.cap)
    out = {
        "run": args.run,
        "archive": CDX,
        "requested": {"matchType": "domain", "all_time_cap": args.cap, "window_from": WINDOW_FROM},
        "wayback_comparison_window": list(WAYBACK_WINDOW),
        "note": ("Read-only CDX queries; no captured page fetched. Window counts are 200 captures with "
                 "asset URLs dropped. Signature hits are over captured URLs only. A zero is absence "
                 "from Arquivo.pt's index, not absence of the page; domain matching includes subdomains, "
                 "so busy general domains (task targets) carry crawler traffic unrelated to the incident. "
                 "Collection 'save' is Arquivo.pt's user-submitted Save Page Now. URL samples are "
                 "truncated to 300 characters with email addresses and credential values redacted."),
        "summary": summary,
        "results": results,
    }
    rendered = json.dumps(out, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
