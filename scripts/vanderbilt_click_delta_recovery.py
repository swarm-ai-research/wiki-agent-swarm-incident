#!/usr/bin/env python3
"""Compare immutable Vanderbilt click snapshots without contacting vanderbi.lt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
KMAD_COMMIT = "2ee3df2fc9a8e7e338be359af7b933de1776f9bc"
PUBLIC_COMMIT = "26bf264895ac0ceed6f075cc2760af9ae1fdc1b2"
LANE_LOG_SHA256 = "2078c883d43053be93ab35640472ac9873be4fd23ad3ea1b68dc0ce46a3e7d96"
LINKS_SHA256 = "377fab954f11f63e3ddd77e030b3a31d21a33fe5b68e3ca27ea9ef8a4cb822f4"
META_SHA256 = "2b9284804b3b1ac7beafe3b4f531eba7a5a0970f0211585852930e146d799c5a"
ALL_TIME = re.compile(
    r"<a href='#stat_line_all'>All time</a></span>\s*"
    r"<span class='historical_count'>([\d,]+) hits?",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_all_time(html: str) -> int | None:
    match = ALL_TIME.search(html)
    return int(match.group(1).replace(",", "")) if match else None


def alias_from_url(url: str) -> str:
    return urlsplit(url).path.strip("/").removesuffix("+")


def load_api_rows(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            alias = row["alias"].casefold()
            if alias not in rows or int(row["clicks"]) > int(rows[alias]["clicks"]):
                rows[alias] = row
    return rows


def load_listing_urls(path: Path) -> list[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("id") == "vanderbilt":
            return row["listing_urls"]
    raise ValueError("missing Vanderbilt venue row")


def build(kmad_root: Path, public_root: Path, venue_path: Path) -> dict:
    lane_dir = kmad_root / "artifacts" / "decode-raw"
    lane_log = lane_dir / "lane5_log.json"
    links_path = public_root / "raw_artifacts" / "shorteners" / "vanderbi.lt" / "links.csv"
    meta_path = public_root / "raw_artifacts" / "shorteners" / "vanderbi.lt" / "meta.json"
    expected = {
        "kmad_lane_log": (lane_log, LANE_LOG_SHA256),
        "public_links": (links_path, LINKS_SHA256),
        "public_meta": (meta_path, META_SHA256),
    }
    repository_paths = {
        "kmad_lane_log": "artifacts/decode-raw/lane5_log.json",
        "public_links": "raw_artifacts/shorteners/vanderbi.lt/links.csv",
        "public_meta": "raw_artifacts/shorteners/vanderbi.lt/meta.json",
    }
    provenance = {}
    for name, (path, expected_hash) in expected.items():
        actual = sha256(path)
        if actual != expected_hash:
            raise ValueError(f"hash mismatch for {path}: {actual}")
        provenance[name] = {"repository_path": repository_paths[name], "sha256": actual}

    api_rows = load_api_rows(links_path)
    captures = json.loads(lane_log.read_text(encoding="utf-8"))
    rows = []
    for capture in captures:
        alias = alias_from_url(capture["url"])
        html_path = lane_dir / capture["raw_file"]
        before = parse_all_time(html_path.read_text(encoding="utf-8", errors="replace"))
        api = api_rows.get(alias.casefold())
        after = int(api["clicks"]) if api else None
        if before is None:
            disposition = "kmad-total-unparseable"
        elif api is None:
            disposition = "absent-from-public-api-export"
        elif after < before:
            disposition = "nonmonotonic-counter"
        elif after == before:
            disposition = "comparable-unchanged"
        else:
            disposition = "comparable-increased"
        rows.append(
            {
                "alias": alias,
                "kmad_captured_at": capture["headers"].get("Date"),
                "kmad_total": before,
                "public_api_total": after,
                "delta": after - before if before is not None and after is not None else None,
                "disposition": disposition,
                "kmad_html": capture["raw_file"],
                "kmad_html_sha256": sha256(html_path),
            }
        )

    by_alias = {row["alias"].casefold(): row for row in rows}
    listing_rows = []
    for url in load_listing_urls(venue_path):
        alias = alias_from_url(url)
        comparison = by_alias.get(alias.casefold())
        listing_rows.append(
            {
                "alias": alias,
                "url": url,
                "kmad_lane5_disposition": comparison["disposition"] if comparison else "no-kmad-lane5-capture",
                "observed_delta_to_public_api": comparison["delta"] if comparison else None,
            }
        )

    comparable = [row for row in rows if row["delta"] is not None]
    return {
        "generated_on": date.today().isoformat(),
        "network_policy": "offline-only; reads two immutable public repository checkouts and the local venue row",
        "provenance": {
            "kmad_repository": {"url": "https://github.com/kmad/agent-swarm-forensics", "commit": KMAD_COMMIT},
            "public_repository": {"url": "https://github.com/brausepulver/collusion-wiki-link-shorteners", "commit": PUBLIC_COMMIT},
            "files": provenance,
            "public_api_fetched_at": json.loads(meta_path.read_text(encoding="utf-8"))["fetched_at"],
        },
        "summary": {
            "kmad_captures": len(rows),
            "comparable_links": len(comparable),
            "increased_links": sum(row["delta"] > 0 for row in comparable),
            "unchanged_links": sum(row["delta"] == 0 for row in comparable),
            "aggregate_delta": sum(row["delta"] for row in comparable),
            "termina_claimed_selected_links": 8,
            "termina_claimed_delta": 121,
            "claim_reproduced": False,
        },
        "termina_listing_dispositions": listing_rows,
        "kmad_population_comparison": rows,
        "conclusion": (
            "The public evidence independently compares 25 of 26 KMAD lane-5 captures and observes "
            "23 increases totaling 266 by the September 5 API export. Termina does not enumerate its "
            "selected eight links, so this different population and interval neither reproduces nor "
            "contradicts the claimed 121-click sum."
        ),
        "limits": [
            "The Termina summary and capture manifest named in its evidence table remain unavailable.",
            "The public API export was fetched after KMAD but before Termina's registered September 6 retrieval.",
            "Counter changes cannot identify callers or distinguish investigators, crawlers, previews, or agents.",
            "The 266 aggregate must not be substituted for the selected-eight 121 aggregate.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kmad-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--venue", type=Path, default=ROOT / "data" / "termina" / "venue.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "vanderbilt_click_delta_recovery_2026-09-08.json")
    args = parser.parse_args()
    report = build(args.kmad_root, args.public_root, args.venue)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
