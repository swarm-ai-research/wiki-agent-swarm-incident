#!/usr/bin/env python3
"""Analyze Termina campaign timelines without equating observations with writes."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
SNAPSHOT = ROOT / "data" / "termina" / "snapshot.json"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
SCAN_ID = re.compile(r"^scan:(?P<venue>[^:]+):(?P<day>\d{4}-\d{2}-\d{2})$")
VERDICT = re.compile(r"scanner verdict (?P<verdict>.+?) on \d+ rows")


def _write_bounds(records: list[dict]) -> dict[str, list[dict]]:
    """Return strong-ID floor, glossary estimate, and RC-overlap ceiling."""
    revision_venues = {
        (row["campaign_id"], row["venue_id"])
        for row in records
        if row["kind"] == "revision"
    }
    lower = []
    central = []
    upper = []
    seen_external = set()
    for row in records:
        kind = row["kind"]
        if kind == "revision":
            lower.append(row)
            central.append(row)
            upper.append(row)
        elif kind == "rc-row":
            upper.append(row)
            if (row["campaign_id"], row["venue_id"]) not in revision_venues:
                central.append(row)
        elif kind in {"paste", "shortlink"}:
            key = (
                row["campaign_id"],
                row["venue_id"],
                kind,
                row["external_id"] or row["id"],
            )
            if key not in seen_external:
                seen_external.add(key)
                lower.append(row)
                central.append(row)
                upper.append(row)
    return {"lower": lower, "central": central, "upper": upper}


def _write_rows(records: list[dict]) -> list[dict]:
    """Return the glossary-aligned central estimate."""
    return _write_bounds(records)["central"]


def _latest_scans(connection: sqlite3.Connection) -> dict[str, dict]:
    connection.row_factory = sqlite3.Row
    latest = {}
    for raw in connection.execute("SELECT * FROM claim WHERE id LIKE 'scan:%'"):
        row = dict(raw)
        match, verdict = SCAN_ID.match(row["id"]), VERDICT.search(row["text"])
        if not match or not verdict:
            continue
        item = {
            "claim_id": row["id"],
            "day": match["day"],
            "status": row["status"],
            "verdict": verdict["verdict"],
            "text": row["text"],
        }
        if match["venue"] not in latest or item["day"] > latest[match["venue"]]["day"]:
            latest[match["venue"]] = item
    return latest


def build(database: Path = DB) -> dict:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        campaigns = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM campaign")}
        records = [dict(row) for row in connection.execute(
            "SELECT * FROM record WHERE campaign_id IS NOT NULL ORDER BY campaign_id,observed_time,id"
        )]
        venues = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM venue")}
        scans = _latest_scans(connection)
    finally:
        connection.close()

    by_campaign = defaultdict(list)
    for row in records:
        by_campaign[row["campaign_id"]].append(row)
    bounds = _write_bounds(records)
    bounds_by_campaign = {name: defaultdict(list) for name in bounds}
    for name, bound_rows in bounds.items():
        for row in bound_rows:
            bounds_by_campaign[name][row["campaign_id"]].append(row)

    campaign_rows = []
    for campaign_id, campaign in sorted(campaigns.items()):
        observed = by_campaign[campaign_id]
        bounded = {
            name: bounds_by_campaign[name][campaign_id]
            for name in ("lower", "central", "upper")
        }
        estimated = bounded["central"]
        days = Counter(
            row["observed_time"][:10]
            for row in estimated
            if row["observed_time"] and DAY.match(row["observed_time"])
        )
        dated = sum(days.values())
        peak_day, peak_writes = (max(days.items(), key=lambda item: item[1]) if days else (None, 0))
        campaign_venues = sorted({row["venue_id"] for row in observed})
        scan_rows = [dict(venue_id=venue, **scans[venue]) for venue in campaign_venues if venue in scans]
        venue_bounds = []
        for venue_id in campaign_venues:
            values = {
                name: sum(row["venue_id"] == venue_id for row in bound_rows)
                for name, bound_rows in bounded.items()
            }
            venue_bounds.append({"venue_id": venue_id, **values})
        campaign_rows.append({
            "id": campaign_id,
            "name": campaign["name"],
            "kind": campaign["kind"],
            "confidence": campaign["confidence"],
            "declared_period": [campaign["period_start"], campaign["period_end"]],
            "observed_period": [
                min((row["observed_time"] for row in observed if row["observed_time"]), default=None),
                max((row["observed_time"] for row in observed if row["observed_time"]), default=None),
            ],
            "observations": len(observed),
            "observation_kinds": dict(sorted(Counter(row["kind"] for row in observed).items())),
            "record_statuses": dict(sorted(Counter(row["status"] for row in observed).items())),
            "phases": dict(sorted(Counter(row["phase"] for row in observed).items())),
            "estimated_writes": len(estimated),
            "write_bounds": {name: len(rows) for name, rows in bounded.items()},
            "venue_write_bounds": venue_bounds,
            "dated_writes": dated,
            "undated_writes": len(estimated) - dated,
            "observation_write_ratio": round(len(observed) / len(estimated), 3) if estimated else None,
            "actors": len({row["actor_id"] for row in observed if row["actor_id"]}),
            "venues": len(campaign_venues),
            "venue_ids": campaign_venues,
            "peak_day": peak_day,
            "peak_writes": peak_writes,
            "peak_share_of_dated_writes": round(peak_writes / dated, 4) if dated else None,
            "latest_scans": scan_rows,
            "scan_coverage": len(scan_rows),
            "scan_verdicts": dict(sorted(Counter(row["verdict"] for row in scan_rows).items())),
        })

    human_venues = {venue_id for venue_id, venue in venues.items() if venue["default_human"]}
    human_scans = [dict(venue_id=venue, **scans[venue]) for venue in sorted(human_venues & scans.keys())]
    return {
        "generated_on": snapshot["generated_at"][:10],
        "snapshot_generated_at": snapshot["generated_at"],
        "schema_version": snapshot["schema_version"],
        "database": str(database.relative_to(ROOT)),
        "write_rule": {
            "source": "https://swarm.termina.digital/db/about.html#glossary",
            "glossary": "writes are distinct saves: revisions where an export exists, one row per listing entry otherwise; saves pair with revisions",
            "lower": "count revisions and unique paste/shortlink external IDs; omit rc-row stand-ins",
            "central": "the glossary-aligned revision-first estimate",
            "upper": "add every rc-row even where a revision stream exists; an observation-overlap ceiling, not a claim that both rows are writes",
            "revision": "count every revision; suppress save and rc-row observations for that campaign/venue",
            "listing_only": "count rc-row when the campaign/venue has no revision stream",
            "paste_shortlink": "deduplicate by campaign, venue, kind, and external_id (record id fallback)",
            "excluded": ["save", "delete", "revert"],
        },
        "campaigns": campaign_rows,
        "human_control_scans": {
            "default_human_venues": len(human_venues),
            "covered_venues": len(human_scans),
            "status_counts": dict(sorted(Counter(row["status"] for row in human_scans).items())),
            "verdicts": dict(sorted(Counter(row["verdict"] for row in human_scans).items())),
            "rows": human_scans,
        },
    }


def _fmt_counts(values: dict) -> str:
    return ", ".join(f"{count} {key}" for key, count in values.items()) or "none"


def markdown(report: dict) -> str:
    lines = [
        "# Termina campaign timelines and detector coverage",
        "",
        "**Source:** pinned [`swarm.termina.digital` schema-v9 SQLite](../data/termina/README.md). "
        "This note compares all eight database campaigns while keeping observation rows, estimated "
        "writes, campaign attribution, and inferred scanner verdicts separate.",
        "",
        "## Observation rows are not writes",
        "",
        "The source glossary defines observations as held rows and writes as distinct saves: revisions "
        "where an export exists, one listing row otherwise; it also says save rows pair with revisions. "
        "The central estimate implements that rule. The lower bound omits listing-only stand-ins; the "
        "upper bound also counts RecentChanges rows beside revision streams because row-level links are "
        "absent. The upper value is therefore an overlap ceiling, not a claim that both observations are writes. "
        "Paste and shortlink IDs are deduplicated in every bound; saves, deletions and reverts are excluded. "
        "See the [published glossary](https://swarm.termina.digital/db/about.html#glossary).",
        "",
        "## Campaign comparison",
        "",
        "| Campaign | Kind / attribution | Observations | Write bounds L/C/U | Obs/central | Actors | Venues | Peak dated writes | Latest scan coverage |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in report["campaigns"]:
        peak = f"{row['peak_day']}: {row['peak_writes']:,} ({row['peak_share_of_dated_writes']:.1%})" if row["peak_day"] else "none dated"
        scans = f"{row['scan_coverage']}/{row['venues']}; {_fmt_counts(row['scan_verdicts'])}"
        ratio = f"{row['observation_write_ratio']:.2f}×" if row["observation_write_ratio"] is not None else "n/a"
        lines.append(
            f"| `{row['id']}` | {row['kind']} / {row['confidence']} | {row['observations']:,} | "
            f"{row['write_bounds']['lower']:,} / {row['write_bounds']['central']:,} / {row['write_bounds']['upper']:,} | "
            f"{ratio} | {row['actors']:,} | "
            f"{row['venues']} | {peak} | {scans} |"
        )
    human = report["human_control_scans"]
    divergent = []
    for campaign in report["campaigns"]:
        for venue in campaign["venue_write_bounds"]:
            spread = venue["upper"] - venue["lower"]
            if spread >= 10:
                divergent.append((spread, campaign["id"], venue))
    divergent.sort(reverse=True)
    lines += [
        "",
        "## Material venue-level uncertainty",
        "",
        "Only campaign/venue pairs with an upper-minus-lower spread of at least 10 are shown. "
        "The spread measures duplicate-observation uncertainty, not statistical confidence.",
        "",
        "| Campaign | Venue | Lower | Central | Upper | Spread |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for spread, campaign_id, venue in divergent:
        lines.append(
            f"| `{campaign_id}` | `{venue['venue_id']}` | {venue['lower']:,} | "
            f"{venue['central']:,} | {venue['upper']:,} | {spread:,} |"
        )
    lines += [
        "",
        "## What the comparison supports",
        "",
        "- The two DSEWiki swarms have the largest observation duplication: revision/save/listing layers "
        "produce far more rows than estimated writes. Paste-only runs are close to one row per write.",
        "- Detector coverage is venue coverage, not campaign proof. A venue can carry several campaigns, "
        "and a latest verdict is an inferred claim about the venue's accumulated rows.",
        "- `usemod-fleet` illustrates the distinction cleanly: six revisions plus twelve listing rows are "
        "six central writes, with bounds 6/6/18; the upper endpoint deliberately exposes the unlinked listing overlap.",
        f"- Human-control coverage is {human['covered_venues']}/{human['default_human_venues']} venues; "
        f"latest verdicts are {_fmt_counts(human['verdicts'])}. `default_human` describes the venue's "
        "baseline, not every row on it.",
        "",
        "## Limits",
        "",
        "All latest scanner claims are `inferred`; the report retains their claim IDs and statuses. "
        "Every campaign-assigned `record.status` is `live` in this snapshot, so there is no non-live "
        "comparison arm; delete observations are `kind=delete`, not records with deleted status. "
        "No scan means unmeasured, not quiet. A quiet venue can still contain agent rows because the "
        "gated detector is tuned to contention, handle grammar and IP spread. Campaign assignments are "
        "the database author's synthesis and do not authenticate a backend agent. Undated shortlinks "
        "remain in write totals but cannot enter daily peaks. The observed record bound can also precede "
        "a campaign's declared start where an undated or earlier artifact was assigned retrospectively.",
        "",
        "Machine-readable campaign rows and every latest scan claim are in "
        "[`data/termina_campaign_analysis_2026-09-08.json`](../data/termina_campaign_analysis_2026-09-08.json).",
        "",
        "Regenerate with:",
        "",
        "```sh",
        "python3 scripts/termina_campaign_analysis.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "termina_campaign_analysis_2026-09-08.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "analysis" / "termina-campaign-analysis.md")
    args = parser.parse_args()
    report = build(args.database)
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({row["id"]: row["estimated_writes"] for row in report["campaigns"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
