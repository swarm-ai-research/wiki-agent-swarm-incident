#!/usr/bin/env python3
"""Turn one swarm-index-watch shard into the committed tick-summary shape.

The watcher (darkfibr/swarm-index-watch with the usemod adapter, see
scripts/swarm_index_watch_tick.sh) appends one JSON event per new index item
to <state dir>/shards/YYYY-MM-DD.jsonl. That shard stays outside the repo; this
script reduces it to counts per venue and per day, the scored share, the
handle stems and authors on the hottest ProWiki venue, and any venue errors, in
the same shape as data/swarm_index_watch_tick_2026-09-07.json. No bodies, no
URLs, no full handle lists: metadata only, so the summary can be committed.

Usage:
  swarm_index_watch_summarize.py <shard.jsonl> [--from-line N] [--run-utc ISO]
      [--focus VENUE] [--out data/swarm_index_watch_tick_YYYY-MM-DD.json]

--from-line skips the first N lines (the shard's length before the tick ran,
so a shard that several ticks wrote to in one UTC day summarises the latest
tick only; the watcher lets the item's own timestamp overwrite the append
time, so there is no per-event clock to filter on). --focus picks the venue for the per-day and stem tables
(default: prowiki-probier when it has new items, else the busiest prowiki-* venue).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_STEM = re.compile(r"^(.*?[A-Za-z])\d*$")


def stem(handle: str) -> str:
    """Drop the trailing digits: Agent010LeminoDirect1781807025 -> Agent010LeminoDirect."""
    m = _STEM.match(handle)
    return m.group(1) if m else handle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shard", type=Path)
    ap.add_argument("--from-line", type=int, default=0,
                    help="skip this many leading lines (shard length before the tick)")
    ap.add_argument("--run-utc", help="tick start, ISO; stamped into the summary")
    ap.add_argument("--focus", help="venue for the per-day and stem tables")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)

    events, errors = [], []
    for i, line in enumerate(a.shard.open()):
        if i < a.from_line or not line.strip():
            continue
        ev = json.loads(line)
        (errors if ev.get("t") == "error" else events).append(ev)

    per_venue = Counter(e["venue"] for e in events)
    per_scored = Counter(e["venue"] for e in events if e.get("score", 0) > 0)
    tier2 = sum(1 for e in events if e.get("tier") == 2)

    focus = a.focus
    if not focus and per_venue.get("prowiki-probier"):
        focus = "prowiki-probier"  # the board under a continuing write burst since disclosure
    if not focus:
        pro = [(n, c) for n, c in per_venue.items() if n.startswith("prowiki-")]
        focus = max(pro, key=lambda nc: nc[1])[0] if pro else (per_venue.most_common(1) or [("", 0)])[0][0]
    fev = [e for e in events if e["venue"] == focus]
    per_day = Counter(
        datetime.fromtimestamp(e["ts"], timezone.utc).strftime("%Y-%m-%d")
        for e in fev if e.get("ts")
    )
    today = max(per_day) if per_day else None
    day_ev = [e for e in fev if e.get("ts")
              and datetime.fromtimestamp(e["ts"], timezone.utc).strftime("%Y-%m-%d") == today]
    pages = [e["title"].split(" [", 1)[0] for e in day_ev if e.get("title")]
    stems = Counter(stem(p) for p in pages)
    authors = Counter(e["author"] for e in day_ev if e.get("author"))

    run_utc = a.run_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    key = focus.replace("-", "_")
    summary = {
        "_about": (
            "One read-only tick of darkfibr/swarm-index-watch (usemod adapter branch) over "
            "data/swarm_index_watch_venues.json, run %s, state persisted outside the repo so "
            "counts are new items since the previous tick, not 30-day windows. Listings are "
            "server-local times converted with each venue's utc_offset_h. Metadata only; no "
            "bodies fetched (fetch_body off everywhere)." % run_utc
        ),
        "run_utc": run_utc,
        "shard": a.shard.name,
        "events_total": len(events),
        "tier2_bodies": tier2,
        "errors": sorted({e["venue"] for e in errors}),
        "per_venue": dict(per_venue.most_common()),
        "per_venue_scored": dict(per_scored.most_common()),
    }
    if focus:
        summary["focus_venue"] = focus
        summary[f"{key}_per_day"] = dict(sorted(per_day.items()))
    if today:
        summary[f"{key}_{today}"] = {
            "edits": len(day_ev),
            "distinct_authors": len(authors),
            "top_handle_stems": [[s, n] for s, n in stems.most_common(12) if n > 1],
            "top_authors": [[s, n] for s, n in authors.most_common(8) if n > 1],
        }
    text = json.dumps(summary, indent=1, ensure_ascii=False) + "\n"
    if a.out:
        a.out.write_text(text)
        print(a.out)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
