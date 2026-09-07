#!/usr/bin/env python3
"""Turn a swarm-index-watch shard into the tick summary committed under data/.

The watcher (darkfibr/swarm-index-watch with the usemod adapter patch in
scripts/) appends one JSON line per new item to <state dir>/shards/<date>.jsonl.
That file stays outside the repo; this script reduces it to the shape of
data/swarm_index_watch_tick_<date>.json: per-venue counts, ProbierWiki per day
and per hour, operator rows, Wiki4D rows, public-board.com thread ids.

    python3 scripts/swarm_index_watch_summarize.py <shard.jsonl> --run-utc 2026-09-07T21:23Z \
        --out data/swarm_index_watch_tick_2026-09-07b.json [--about "..."]

Timestamps in the shard are what the venue printed (ProWiki: server-local CEST)
unless the venue config sets utc_offset_h; item ids hash (page, date, time,
author) at minute resolution, so same-minute saves of one page collapse.
"""
import argparse
import collections
import datetime
import json
import re

AWS_PREFIXES = ("3.", "18.", "23.", "34.", "44.", "50.", "52.", "54.", "98.", "100.", "107.", "174.", "184.")
OPERATORS = {"HelmutLeitner", "GertMUC", "FranzNahrada", "MarkusLude"}


def when(event):
    ts = event.get("ts")
    return datetime.datetime.fromtimestamp(ts, datetime.UTC) if ts else None


def day(event):
    d = when(event)
    return d.strftime("%Y-%m-%d") if d else None


def stem(title):
    return re.sub(r"\d+.*$", "", (title or "").split(" [")[0])


def summarize(events, run_utc, about=None, focus_day=None, first_tick_cutoff=None):
    for e in events:
        e.setdefault("author", None)
        e.setdefault("title", "")
    per_venue = collections.Counter(e["venue"] for e in events)
    scored = collections.Counter(e["venue"] for e in events if (e.get("score") or 0) > 0)
    probier = [e for e in events if e["venue"] == "prowiki-probier"]
    focus_day = focus_day or run_utc[:10]
    p_day = [e for e in probier if day(e) == focus_day]
    out = {
        "_about": about or f"Read-only tick of darkfibr/swarm-index-watch over data/swarm_index_watch_venues.json, run {run_utc}. "
                            "Listings are 30-day windows; ProWiki times are server-local (CEST) as printed; item ids collapse same-minute saves of one page.",
        "run_utc": run_utc,
        "events_total": len(events),
        "per_venue": dict(per_venue.most_common()),
        "per_venue_scored": dict(scored.most_common()),
        "prowiki_probier_per_day": dict(sorted(collections.Counter(day(e) for e in probier if day(e)).items())),
        f"prowiki_probier_{focus_day}": {
            "edits": len(p_day),
            "distinct_authors": len({e["author"] for e in p_day}),
            "per_hour_local": dict(sorted(collections.Counter(when(e).strftime("%H") for e in p_day).items())),
            "aws_like_authors": sum(1 for e in p_day if (e["author"] or "").startswith(AWS_PREFIXES)),
            "top_title_stems": collections.Counter(stem(e["title"]) for e in p_day).most_common(12),
            "top_authors": collections.Counter(e["author"] for e in p_day).most_common(8),
        },
        "operator_rows": [[when(e).strftime("%m-%d %H:%M"), e["venue"], e["author"], e["title"]]
                          for e in events if e["author"] in OPERATORS and when(e)],
        "prowiki_wiki4d": [[day(e), when(e).strftime("%H:%M") if when(e) else None, e["author"], e["title"]]
                           for e in events if e["venue"] == "prowiki-wiki4d"],
        "public_board_threads": [e["id"] for e in events if e["venue"] == "public-board.com"],
    }
    if first_tick_cutoff:
        cut = datetime.datetime.fromisoformat(first_tick_cutoff).replace(tzinfo=datetime.UTC).timestamp()
        out[f"prowiki_probier_{focus_day}"]["saves_after_cutoff"] = sum(1 for e in p_day if e["ts"] >= cut)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard")
    ap.add_argument("--run-utc", required=True, help="e.g. 2026-09-07T21:23Z")
    ap.add_argument("--out", required=True)
    ap.add_argument("--about")
    ap.add_argument("--focus-day", help="YYYY-MM-DD in the venue's printed clock; default: run date")
    ap.add_argument("--cutoff", help="naive local ISO time; count focus-day saves at or after it")
    a = ap.parse_args()
    events = [json.loads(line) for line in open(a.shard) if line.strip()]
    out = summarize(events, a.run_utc, a.about, a.focus_day, a.cutoff)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"{a.out}: {out['events_total']} events, {len(out['per_venue'])} venues")


if __name__ == "__main__":
    main()
