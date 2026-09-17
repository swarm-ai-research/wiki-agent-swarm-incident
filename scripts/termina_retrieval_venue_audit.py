#!/usr/bin/env python3
"""Audit the Termina 870-body retrieval-venue classification claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
CLAIM_ID = "retrieval-venues-never-talk"
VENUES = ("probier", "fractal")
PUBLISHED_COUNTS = {
    "probier": {"data-cache": 166, "bridge": 235},
    "fractal": {"data-cache": 191, "bridge": 30},
}
CLAIMED_DENOMINATOR = 870
DECLARED_UNATTACHED = {"ambiguous_minutes": 104, "pack_entries_without_rc_row": 192}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def build(database: Path = DB, body_root: Path = ROOT) -> dict:
    """Build a metadata-only audit; never copy revision bodies into the artifact."""
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        claim_row = connection.execute(
            "SELECT * FROM claim WHERE id = ?", (CLAIM_ID,)
        ).fetchone()
        if claim_row is None:
            raise ValueError(f"missing required claim {CLAIM_ID!r}")
        claim = dict(claim_row)
        evidence = dict(
            connection.execute(
                "SELECT * FROM evidence WHERE id = ?", (claim["made_by"],)
            ).fetchone()
        )

        placeholders = ",".join("?" for _ in VENUES)
        records = [
            dict(row)
            for row in connection.execute(
                f"""SELECT id, venue_id, title, content_kind, body_path,
                           body_sha256, body_len, source, collection_id
                    FROM record
                    WHERE venue_id IN ({placeholders})
                    ORDER BY venue_id, id""",
                VENUES,
            )
        ]
    finally:
        connection.close()

    category_counts: dict[str, dict[str, int]] = {}
    for venue in VENUES:
        category_counts[venue] = dict(
            sorted(Counter(
                row["content_kind"] or "unclassified"
                for row in records
                if row["venue_id"] == venue
            ).items())
        )

    published_checks = []
    for venue, expected in PUBLISHED_COUNTS.items():
        for category, count in expected.items():
            observed = category_counts[venue].get(category, 0)
            published_checks.append(
                {
                    "venue": venue,
                    "category": category,
                    "published": count,
                    "database": observed,
                    "matches": observed == count,
                }
            )

    path_rows = [row for row in records if row["body_path"]]
    present_paths = [
        row for row in path_rows if (body_root / row["body_path"]).is_file()
    ]
    hashed_rows = [row for row in records if row["body_sha256"]]
    categorized_total = sum(
        item["published"] for item in published_checks
    )
    database_comparable_total = sum(
        item["database"] for item in published_checks
    )
    samples = {}
    for venue in VENUES:
        samples[venue] = {}
        for category in ("data-cache", "bridge", "probe-test"):
            samples[venue][category] = [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "body_sha256": row["body_sha256"],
                    "body_available": bool(
                        row["body_path"] and (body_root / row["body_path"]).is_file()
                    ),
                }
                for row in records
                if row["venue_id"] == venue and row["content_kind"] == category
            ][:3]

    denominator_reproducible = False
    bodies_reviewable = len(present_paths) == CLAIMED_DENOMINATOR
    negative_rows = sum(
        category_counts[venue].get(category, 0)
        for venue in VENUES
        for category in ("coordination", "answer-share")
    )
    overall_status = "reported-not-independently-reproduced"

    return {
        "generated_on": date.today().isoformat(),
        "database": {
            "path": _relative(database),
            "sha256": sha256_file(database),
        },
        "claim": claim,
        "evidence": evidence,
        "published_claim": {
            "denominator": CLAIMED_DENOMINATOR,
            "category_counts": PUBLISHED_COUNTS,
            "category_subtotal": categorized_total,
            "implied_probe_test_remainder": CLAIMED_DENOMINATOR - categorized_total,
            "declared_unattached": DECLARED_UNATTACHED,
        },
        "rules": {
            "reported_inclusion": "revision bodies attached from shellac's wiki-additions for probier and fractal",
            "reported_exclusion": "104 ambiguous minutes and 192 pack entries without a RecentChanges row were left unattached",
            "reproducibility_finding": "No 870-row manifest or stable row selector is stored in the pinned database, so inclusion and exclusion cannot be replayed.",
            "database_count_rule": "Group every pinned probier/fractal record by venue_id and content_kind; this checks derivative label totals, not body classification.",
            "review_protocol": {
                "coordination": "direct peer address, request, status, deadline, or work allocation",
                "answer-share": "a task answer or answer-bearing result offered for peer reuse",
                "data-cache": "retrieved data or API output without peer-directed language",
                "bridge": "a link, proxy, or locator whose purpose is access to stored material",
                "probe-test": "a connectivity, formatting, storage, or retrieval-path test",
                "precedence": "coordination or answer-share wins over storage/tooling labels when both are present",
                "provenance": "Authored review protocol for a future independent audit; the original classifier rules were not preserved.",
            },
        },
        "database_observations": {
            "records_in_two_venues": len(records),
            "category_counts": category_counts,
            "published_count_checks": published_checks,
            "published_counts_all_match": all(row["matches"] for row in published_checks),
            "published_count_cells_matching": sum(row["matches"] for row in published_checks),
            "database_comparable_subtotal": database_comparable_total,
            "coordination_or_answer_share_labels": negative_rows,
            "rows_with_body_sha256": len(hashed_rows),
            "unique_body_sha256": len({row["body_sha256"] for row in hashed_rows}),
            "rows_with_body_path": len(path_rows),
            "body_paths_present": len(present_paths),
            "body_paths_absent": len(path_rows) - len(present_paths),
        },
        "representative_review": {
            "metadata_samples": samples,
            "false_positive_rows_reviewed": 0,
            "false_negative_rows_reviewed": 0,
            "result": "not-performed",
            "reason": "The body pack and the 870-row inclusion manifest are absent; hashes, lengths, titles, and inherited labels cannot support a content-error review.",
            "redistributed_body_text": False,
        },
        "assessment": {
            "category_subtotals": "not-reproduced-from-current-derivative-labels",
            "claimed_denominator": "not-reproducible",
            "zero_coordination_or_answer_share": "not-independently-reproducible",
            "overall_evidence_status": overall_status,
            "why": "Only one of the four stated data-cache/bridge cells matches the current database. The corpus selector, original classification rules, and revision bodies needed to explain the drift or test the negative claim are unavailable.",
            "denominator_reproducible": denominator_reproducible,
            "bodies_reviewable": bodies_reviewable,
        },
    }


def markdown(report: dict) -> str:
    observations = report["database_observations"]
    claim = report["published_claim"]
    checks = observations["published_count_checks"]
    lines = [
        "# Retrieval-venue no-coordination reproduction",
        "",
        "**Result:** only one of the four published `data-cache` and `bridge` cells matches "
        "the current pinned database. The **870-body denominator and zero-coordination "
        "conclusion do not independently reproduce**. The appropriate archive status is "
        "**reported, not independently reproduced**.",
        "",
        "## What reproduces",
        "",
        "| Venue | Category | Published | Pinned database | Match |",
        "|---|---|---:|---:|---|",
    ]
    for row in checks:
        lines.append(
            f"| `{row['venue']}` | {row['category']} | {row['published']} | "
            f"{row['database']} | {'yes' if row['matches'] else 'no'} |"
        )
    lines += [
        "",
        f"The published cells total **{claim['category_subtotal']}**; the same cells in the "
        f"current snapshot total **{observations['database_comparable_subtotal']}**. The claimed denominator "
        f"of **{claim['denominator']}** therefore implies **{claim['implied_probe_test_remainder']}** "
        "probe-test rows, but the pinned records contain many more probe-test labels and no "
        "stored selector identifies which rows made up that remainder.",
        "",
        "The pinned database has zero `coordination` or `answer-share` labels for the two "
        "venues. That is a restatement of the stored classification, not an independent search "
        "of the bodies.",
        "",
        "## Denominator and body availability",
        "",
        f"The database holds **{observations['records_in_two_venues']}** probier/fractal record "
        f"rows, **{observations['rows_with_body_sha256']}** body hashes, and "
        f"**{observations['rows_with_body_path']}** body-path references. In this checkout, "
        f"**{observations['body_paths_present']}** referenced bodies are present and "
        f"**{observations['body_paths_absent']}** are absent. A hash proves identity if a body "
        "is later obtained; it does not permit content classification by itself.",
        "",
        "The source note says 104 ambiguous minutes and 192 reading-pack entries without a "
        "RecentChanges row were excluded. It does not preserve their identifiers or an included "
        "870-row manifest. Consequently there is no reproducible inclusion/exclusion rule in the "
        "available artifact.",
        "",
        "## False-positive and false-negative review",
        "",
        "No body-level error review was performed: **0 false-positive candidates and 0 false-"
        "negative candidates were reviewable**. Publishing examples based only on titles, hashes, "
        "or inherited labels would manufacture evidence. The JSON artifact instead publishes "
        "three metadata-only sample rows per venue/category and redistributes no body text.",
        "",
        "For a future review, the artifact records an explicit protocol: peer-directed requests, "
        "statuses, deadlines, or allocations are `coordination`; answers offered for reuse are "
        "`answer-share`; either label takes precedence over cache/bridge/probe labels. This is an "
        "authored audit protocol, not a recovered version of the original classifier.",
        "",
        "## Bounded conclusion",
        "",
        "The available data support only that the current Termina snapshot stores one matching "
        "category cell and no coordination/answer-share labels on probier or fractal. They do **not** establish "
        "that all 870 claimed bodies were inspected under reproducible rules or that false negatives "
        "were absent. The stronger sentence—\"the swarm that cached most never addressed a peer on "
        "its own venues\"—remains reported secondary evidence.",
        "",
        "To complete an independent reproduction, obtain the exact 870-row manifest, the original "
        "classifier/version and rules, and hash-matching bodies for those rows. Then review a "
        "stratified sample plus every coordination/answer-share candidate without checking body text "
        "into this repository.",
        "",
        "Regenerate the metadata audit:",
        "",
        "```sh",
        "python3 scripts/termina_retrieval_venue_audit.py",
        "```",
        "",
        "The machine-readable result is "
        "[`data/termina_retrieval_venue_audit_2026-09-08.json`](../data/termina_retrieval_venue_audit_2026-09-08.json).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--body-root", type=Path, default=ROOT)
    parser.add_argument(
        "--json",
        type=Path,
        default=ROOT / "data" / "termina_retrieval_venue_audit_2026-09-08.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT / "analysis" / "termina-retrieval-venue-audit.md",
    )
    args = parser.parse_args()
    report = build(args.database, args.body_root)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown.write_text(markdown(report))
    print(
        f"audited {report['database_observations']['records_in_two_venues']} records; "
        f"status={report['assessment']['overall_evidence_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
