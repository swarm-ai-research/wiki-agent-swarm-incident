#!/usr/bin/env python3
"""Do Arquivo.pt saves and the wiki writes cite the same URLs, and in what order?

Usage:
  python3 scripts/arquivo_export_timing.py --raw /tmp/arquivo_raw.json \\
      --revisions /path/to/full-wiki-logs/revisions.jsonl \\
      --out data/arquivo_export_url_timing_2026-09-14.json

The sweep ([arquivo-pt-sweep.md](../analysis/arquivo-pt-sweep.md)) shows what
Arquivo.pt's Save Page Now holds. It cannot show who submitted it. This script
asks a narrower question the two records can answer together: is a URL saved to
Arquivo.pt the *same* URL a wiki revision later cites, and which came first?

A match is exact after normalization (scheme, `www.`, trailing slash and case
dropped). For proxy and reader captures the URL embedded inside the capture is
matched too, so a save of `api.microlink.io/?url=<target>` matches a wiki
citation of `<target>`.

Generic endpoints match by coincidence: every fleet tests `example.com` and
`httpbin.org/get`. Matches are therefore split into task-specific URLs and a
generic list (`GENERIC_HOSTS`, plus bare hosts and `robots.txt`), and only the
task-specific ones are summarized.

Output is counts, timestamps, revision IDs and normalized URLs. No revision
text, and URLs are truncated and redacted as in the sweep.
"""
import argparse
import calendar
import json
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arquivo_cdx_sweep import NOISE, collection, redact  # noqa: E402

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\]\)\|}]+", re.I)
EMBEDDED_URL = re.compile(r"https?(?::/+|/)([A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s]*)")
GENERIC_HOSTS = {"example.com", "example.org", "example.net", "httpbin.org", "postman-echo.com",
                 "echoserver.dev", "r.jina.ai", "api.allorigins.win", "corsproxy.io",
                 "test.cors.workers.dev", "api.codetabs.com", "markdown.new", "pure.md",
                 "images.weserv.nl", "api.microlink.io", "proxymule.com", "allorigins.hexlet.app"}


def normalize(url):
    url = url.strip().rstrip(".,;)'\"")
    url = re.sub(r"^https?://", "", url, flags=re.I).lower()
    return re.sub(r"^www\.", "", url).rstrip("/")


def is_generic(normalized):
    host = normalized.split("/")[0]
    path = normalized[len(host):].strip("/")
    return host in GENERIC_HOSTS or not path or path == "robots.txt"


def embedded(url):
    parts = urllib.parse.urlsplit(url)
    found = EMBEDDED_URL.search(urllib.parse.unquote(parts.path + "?" + parts.query))
    return found.group(1) if found else None


def capture_seconds(stamp):
    return calendar.timegm(time.strptime(stamp[:14], "%Y%m%d%H%M%S"))


def revision_seconds(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def first_mentions(revisions_path):
    """Earliest revision citing each URL. The export repeats bodies forward, so
    the first mention is the write that introduced the URL to the wikis."""
    first = {}
    count = 0
    with open(revisions_path, encoding="utf-8") as handle:
        for line in handle:
            revision = json.loads(line)
            count += 1
            for url in set(URL_IN_TEXT.findall(revision["body"])):
                key = normalize(url)
                stamp = revision["time"]
                if key not in first or stamp < first[key]["utc"]:
                    first[key] = {"utc": stamp, "rev_id": revision["rev_id"], "editor": revision["label"]}
    return first, count


def saves(raw):
    for host, record in raw.items():
        for row in record.get("window_rows") or []:
            if str(row.get("status")) == "200" and collection(row) == "save" and not NOISE.search(row["url"]):
                yield host, row


def build(raw, revisions_path):
    first, revisions = first_mentions(revisions_path)
    matches, capture_count = [], 0
    for host, row in saves(raw):
        capture_count += 1
        target = embedded(row["url"])
        for key in {normalize(row["url"])} | ({normalize(target)} if target else set()):
            mention = first.get(key)
            if not mention:
                continue
            matches.append({
                "url": key[:200],
                "generic": is_generic(key),
                "capture": {"host": host, "ts": row["timestamp"], "url": redact(row["url"])[:200]},
                "first_wiki_mention": mention,
                "wiki_minus_capture_seconds": revision_seconds(mention["utc"]) - capture_seconds(row["timestamp"]),
            })
    specific = [m for m in matches if not m["generic"]]
    leads = sorted(m["wiki_minus_capture_seconds"] for m in specific)
    by_url = defaultdict(list)
    for match in specific:
        by_url[match["url"]].append(match["wiki_minus_capture_seconds"])
    saved_first = [url for url, deltas in by_url.items() if max(deltas) > 0]
    return {
        "schema": "arquivo_export_url_timing_v1",
        "inputs": {
            "arquivo": "data/arquivo_cdx_surfaces_2026-09-14.json (same raw CDX rows)",
            "export": "fast-follow-question-trajectories full-wiki-logs.zip#revisions.jsonl",
            "export_revisions": revisions,
        },
        "note": ("Exact normalized URL matches between Arquivo.pt Save Page Now captures and the "
                 "earliest wiki revision citing the same URL. Positive seconds mean the capture "
                 "came first. Generic endpoints (example.com, httpbin.org, bare proxy hosts, "
                 "robots.txt) match by coincidence and are kept separate. A match shows the same "
                 "URL in both records, not that the same actor produced both."),
        "summary": {
            "save_captures_scanned": capture_count,
            "matches": len(matches),
            "task_specific_matches": len(specific),
            "generic_matches": len(matches) - len(specific),
            "task_specific_urls": len(by_url),
            "urls_saved_before_first_wiki_mention": len(saved_first),
            "capture_first": sum(1 for x in leads if x > 0),
            "wiki_first": sum(1 for x in leads if x < 0),
            "median_lead_seconds": leads[len(leads) // 2] if leads else None,
            "hosts": dict(sorted(Counter(m["capture"]["host"] for m in specific).items())),
        },
        "matches": sorted(matches, key=lambda m: (m["capture"]["ts"], m["url"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--revisions", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = build(json.loads(args.raw.read_text()), args.revisions)
    rendered = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
