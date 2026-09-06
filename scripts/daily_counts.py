#!/usr/bin/env python3
"""Aggregate the public forensic export into per-UTC-day counts for timeline.html.

Reads JoshuaDavid/WikiAgentSwarmInvestigation (not redistributed here) straight from
GitHub, and writes data/daily_counts.json. Saves and deletions for the ProWiki farm
(dse, probier, fractal, dorfwiki) come from agent-logs/prowiki/events.jsonl; the
five smaller wikis come from their own revisions.jsonl. Only days on or after
2026-05-01 are kept, matching the export's write_date cut.

    python3 scripts/daily_counts.py            # writes data/daily_counts.json
"""
import collections, json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation/HEAD/agent-logs/"
GROUP = {"dse": "dse", "probier": "probier", "fractal": "fractal", "wiki4d": "wiki4d"}
SMALL = ["wiki4d", "apchem", "texteditors", "ludism", "milkwiki"]
CUT = "2026-05-01"


def lines(path):
    with urllib.request.urlopen(RAW + path) as r:
        for line in r:
            yield json.loads(line)


def day(t):
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except (ValueError, AttributeError):
        return None


def main():
    saves = collections.defaultdict(collections.Counter)
    dels = collections.Counter()
    for e in lines("prowiki/events.jsonl"):
        d = day(e["time"])
        if not d or d < CUT:
            continue
        if e["event_type"] == "save":
            saves[d][GROUP.get(e["wiki"], "other")] += 1
        elif e["event_type"] == "delete":
            dels[d] += 1
    for w in SMALL:
        for r in lines(f"{w}/revisions.jsonl"):
            d = day(r["time"])
            if d and d >= CUT:
                saves[d][GROUP.get(w, "other")] += 1
    days = sorted(set(saves) | set(dels))
    rows = [{"d": d, **{k: saves[d][k] for k in ("dse", "probier", "fractal", "wiki4d", "other")}, "del": dels[d]} for d in days]
    out = Path(__file__).resolve().parent.parent / "data" / "daily_counts.json"
    out.write_text(json.dumps({
        "source": "JoshuaDavid/WikiAgentSwarmInvestigation agent-logs/* (fetched " + datetime.now(timezone.utc).date().isoformat() + ")",
        "day": "UTC",
        "fields": {"dse": "DSEWiki saves", "probier": "ProbierWiki saves", "fractal": "FractalWiki saves",
                   "wiki4d": "Wiki4D revisions", "other": "apchem + texteditors + milkwiki + ludism + dorfwiki",
                   "del": "admin deletions (all DSEWiki)"},
        "rows": rows}, indent=1))
    print(f"{len(rows)} days, {sum(sum(saves[d].values()) for d in days)} saves, {sum(dels.values())} deletions -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
