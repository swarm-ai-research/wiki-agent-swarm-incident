#!/usr/bin/env python3
"""Test the reported alphabetical-teardown / ZZZ-backup story against the export.

Two claims travel together in the press summaries of the DSEWiki incident: that
the moderator deleted pages in alphabetical order, and that the agents evaded
this by creating `ZZZ`-prefixed backup copies. The incident database holds 5,217
delete events with titles and timestamps, so both are checkable.

Ordering is measured against a shuffled null of the same titles: a global A-Z
sweep would give near-100% ascending adjacent pairs, whereas locally sorted
passes give long ascending runs inside bursts with no global trend. Evasion is
measured as the survival rate of Z-prefixed titles against the base rate for all
titles on the venue.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime
from pathlib import Path


DB = Path(__file__).resolve().parents[1] / "data" / "termina" / "incidents.sqlite"
BURST_GAP_SECONDS = 600
MIN_BURST = 10
SHUFFLES = 200


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def _bursts(rows: list[tuple[str, str]]) -> list[list[str]]:
    """Split time-ordered (timestamp, title) rows on gaps longer than BURST_GAP_SECONDS."""
    out: list[list[str]] = []
    current = [rows[0][1]]
    for previous, row in zip(rows, rows[1:]):
        if (_parse(row[0]) - _parse(previous[0])).total_seconds() > BURST_GAP_SECONDS:
            out.append(current)
            current = []
        current.append(row[1])
    out.append(current)
    return [burst for burst in out if len(burst) >= MIN_BURST]


def _ascending_runs(bursts: list[list[str]]) -> tuple[int, int, int, int]:
    """Return (longest run, runs of 5+, ascending pairs, total pairs)."""
    longest = biggest = ascending = pairs = 0
    for burst in bursts:
        run = 1
        for left, right in zip(burst, burst[1:]):
            pairs += 1
            if right.lower() >= left.lower():
                ascending += 1
                run += 1
                continue
            longest = max(longest, run)
            biggest += run >= 5
            run = 1
        longest = max(longest, run)
        biggest += run >= 5
    return longest, biggest, ascending, pairs


def ordering(connection: sqlite3.Connection, venue: str, seed: int) -> dict:
    rows = connection.execute(
        "select observed_time, title from record"
        " where venue_id = ? and kind = 'delete' and title is not null"
        " order by observed_time",
        (venue,),
    ).fetchall()
    bursts = _bursts([(str(a), str(b)) for a, b in rows])
    longest, biggest, ascending, pairs = _ascending_runs(bursts)

    rng = random.Random(seed)
    null_longest = null_biggest = 0
    for _ in range(SHUFFLES):
        shuffled = [rng.sample(burst, len(burst)) for burst in bursts]
        run_longest, run_biggest, _, _ = _ascending_runs(shuffled)
        null_longest += run_longest
        null_biggest += run_biggest

    return {
        "deletes": len(rows),
        "window": [str(rows[0][0]), str(rows[-1][0])],
        "bursts": len(bursts),
        "ascending_pair_share": round(ascending / pairs, 4),
        "longest_ascending_run": longest,
        "runs_of_5_or_more": biggest,
        "null_longest_ascending_run": round(null_longest / SHUFFLES, 1),
        "null_runs_of_5_or_more": round(null_biggest / SHUFFLES, 1),
    }


def evasion(connection: sqlite3.Connection, venue: str) -> dict:
    def titles(where: str) -> set[str]:
        sql = (
            "select distinct title from record"
            f" where venue_id = ? and title is not null and {where}"
        )
        return {str(row[0]) for row in connection.execute(sql, (venue,))}

    written = titles("kind in ('revision', 'save')")
    deleted = titles("kind = 'delete'")
    out = {}
    for label, subset in (
        ("all", written),
        ("z_prefixed", {t for t in written if t.upper().startswith("Z")}),
        ("zzz_anywhere", {t for t in written if "ZZZ" in t.upper()}),
    ):
        survivors = subset - deleted
        out[label] = {
            "pages": len(subset),
            "deleted": len(subset) - len(survivors),
            "survival_rate": round(len(survivors) / len(subset), 4) if subset else None,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--venue", default="dse")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    try:
        report = {
            "venue": args.venue,
            "ordering": ordering(connection, args.venue, args.seed),
            "evasion": evasion(connection, args.venue),
        }
    finally:
        connection.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
