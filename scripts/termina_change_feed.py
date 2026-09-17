#!/usr/bin/env python3
"""Compare two validated Termina snapshots and emit meaningful review events."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import verify_termina_snapshot


SCAN_ID = re.compile(r"^scan:(?P<venue>[^:]+):(?P<day>\d{4}-\d{2}-\d{2})$")
SCAN_TEXT = re.compile(r"^scanner verdict (?P<verdict>.+?) on (?P<rows>\d+) rows(?:\:|$)")


def _table(connection: sqlite3.Connection, name: str) -> dict[str, dict]:
    connection.row_factory = sqlite3.Row
    return {row["id"]: dict(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY id')}


def _latest_scans(claims: dict[str, dict]) -> dict[str, dict]:
    scans = {}
    for claim in claims.values():
        identity = SCAN_ID.match(claim["id"])
        if not identity:
            continue
        if claim["status"] != "inferred":
            raise ValueError(f"scanner claim {claim['id']} has non-inferred status {claim['status']!r}")
        observation = SCAN_TEXT.match(claim["text"])
        if not observation:
            raise ValueError(f"scanner claim {claim['id']} has unrecognized text")
        item = {
            "claim_id": claim["id"],
            "venue": identity["venue"],
            "day": identity["day"],
            "verdict": observation["verdict"],
            "rows": int(observation["rows"]),
            "evidence_status": "inferred",
        }
        previous = scans.get(item["venue"])
        if previous is None or item["day"] > previous["day"]:
            scans[item["venue"]] = item
    return scans


def _claim_summary(claim: dict) -> dict:
    return {
        "id": claim["id"],
        "status": claim["status"],
        "subject_kind": claim["subject_kind"],
        "subject_id": claim["subject_id"],
    }


def compare_databases(before: Path, after: Path) -> dict:
    old_connection = sqlite3.connect(f"file:{before}?mode=ro", uri=True)
    new_connection = sqlite3.connect(f"file:{after}?mode=ro", uri=True)
    try:
        old_claims, new_claims = _table(old_connection, "claim"), _table(new_connection, "claim")
        old_evidence, new_evidence = _table(old_connection, "evidence"), _table(new_connection, "evidence")
    finally:
        old_connection.close()
        new_connection.close()

    old_scan_ids = {key for key in old_claims if SCAN_ID.match(key)}
    new_scan_ids = {key for key in new_claims if SCAN_ID.match(key)}
    old_regular = set(old_claims) - old_scan_ids
    new_regular = set(new_claims) - new_scan_ids
    added_claims = [_claim_summary(new_claims[key]) for key in sorted(new_regular - old_regular)]
    removed_claims = [_claim_summary(old_claims[key]) for key in sorted(old_regular - new_regular)]
    claim_status_changes = []
    evidence_link_changes = []
    for key in sorted(old_regular & new_regular):
        old, new = old_claims[key], new_claims[key]
        if old["status"] != new["status"]:
            claim_status_changes.append({"id": key, "before": old["status"], "after": new["status"]})
        for field in ("made_by", "checked_by"):
            if old[field] != new[field]:
                evidence_link_changes.append({"claim_id": key, "field": field, "before": old[field], "after": new[field]})

    evidence_changes = []
    for key in sorted(set(old_evidence) | set(new_evidence)):
        if key not in old_evidence:
            evidence_changes.append({"id": key, "change": "added", "kind": new_evidence[key]["kind"]})
        elif key not in new_evidence:
            evidence_changes.append({"id": key, "change": "removed", "kind": old_evidence[key]["kind"]})
        elif old_evidence[key] != new_evidence[key]:
            changed_fields = sorted(field for field in new_evidence[key] if old_evidence[key].get(field) != new_evidence[key].get(field))
            evidence_changes.append({"id": key, "change": "changed", "fields": changed_fields})

    old_scans, new_scans = _latest_scans(old_claims), _latest_scans(new_claims)
    scan_coverage = []
    verdict_transitions = []
    row_coverage_changes = []
    for venue in sorted(set(old_scans) | set(new_scans)):
        old, new = old_scans.get(venue), new_scans.get(venue)
        if old is None:
            scan_coverage.append({"venue": venue, "change": "added", "after": new})
        elif new is None:
            scan_coverage.append({"venue": venue, "change": "removed", "before": old})
        else:
            if old["verdict"] != new["verdict"]:
                verdict_transitions.append({"venue": venue, "before": old, "after": new, "evidence_status": "inferred"})
            if old["rows"] != new["rows"]:
                row_coverage_changes.append({"venue": venue, "before": old, "after": new, "delta": new["rows"] - old["rows"], "evidence_status": "inferred"})

    events = {
        "claim_additions": added_claims,
        "claim_removals": removed_claims,
        "claim_status_changes": claim_status_changes,
        "evidence_link_changes": evidence_link_changes,
        "evidence_changes": evidence_changes,
        "scan_coverage_changes": scan_coverage,
        "scan_verdict_transitions": verdict_transitions,
        "scan_row_coverage_changes": row_coverage_changes,
    }
    return {
        "summary": {name: len(items) for name, items in events.items()},
        "events": events,
        "policy": {
            "scanner_status": "inferred",
            "unchanged_daily_scans": "suppressed after comparing each venue's latest date",
            "claim_status_changes": "reported verbatim; never automatically promoted",
        },
    }


def compare_snapshot_dirs(before: Path, after: Path) -> dict:
    before_validation = verify_termina_snapshot.verify(before)
    after_validation = verify_termina_snapshot.verify(after)
    report = compare_databases(before / "incidents.sqlite", after / "incidents.sqlite")
    report["before"] = before_validation
    report["after"] = after_validation
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True, help="Earlier validated snapshot directory")
    parser.add_argument("--after", type=Path, required=True, help="Later validated snapshot directory")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = compare_snapshot_dirs(args.before, args.after)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
