#!/usr/bin/env python3
"""Audit the Vanderbilt click-delta claim without contacting live shortlinks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
CLAIM_ID = "vanderbilt-post-publication-clicks"
RECOVERY_REPORT = ROOT / "data" / "vanderbilt_click_delta_recovery_2026-09-08.json"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: str) -> int:
    return int(value) if value.isdigit() else NUMBER_WORDS[value.casefold()]


def parse_claim(text: str) -> dict:
    pattern = re.compile(
        r"(?P<links>\w+) links gained (?P<delta>\d+) clicks between the september 4 .*? and september 6 .*?; "
        r"(?P<no_referrer>\d+) have no recorded referrer and (?P<one_referrer>\w+) have one",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError("Vanderbilt claim text no longer matches the audited structure")
    values = {key: _number(value) for key, value in match.groupdict().items()}
    values["referrer_partition_total"] = values["no_referrer"] + values["one_referrer"]
    values["partition_matches_delta"] = values["referrer_partition_total"] == values["delta"]
    return values


def _artifact(evidence: dict, evidence_root: Path) -> dict:
    declared = evidence.get("path")
    candidate = evidence_root / declared if declared else None
    present = bool(candidate and candidate.is_file())
    actual = sha256(candidate) if present else None
    return {
        "evidence_id": evidence["id"],
        "kind": evidence["kind"],
        "publisher": evidence["publisher"],
        "declared_path": declared,
        "declared_sha256": evidence.get("sha256"),
        "present": present,
        "actual_sha256": actual,
        "hash_matches": present and actual == evidence.get("sha256"),
        "url_recorded_but_not_fetched": evidence.get("url"),
        "published": evidence.get("published"),
        "retrieved_at": evidence.get("retrieved_at"),
    }


def build(database: Path = DB, evidence_root: Path = ROOT, recovery_report: Path = RECOVERY_REPORT) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        claim_row = connection.execute("SELECT * FROM claim WHERE id=?", (CLAIM_ID,)).fetchone()
        if claim_row is None:
            raise ValueError(f"missing claim {CLAIM_ID}")
        claim = dict(claim_row)
        evidence_ids = [claim["made_by"], claim["checked_by"]]
        evidence = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM evidence WHERE id IN (?, ?)", evidence_ids
            )
        }
    finally:
        connection.close()
    artifacts = [_artifact(evidence[evidence_id], evidence_root) for evidence_id in evidence_ids]
    arithmetic = parse_claim(claim["text"])
    all_held = all(item["present"] and item["hash_matches"] for item in artifacts)
    independent = None
    if recovery_report.is_file():
        recovered = json.loads(recovery_report.read_text(encoding="utf-8"))
        independent = {
            "summary": recovered["summary"],
            "provenance": recovered["provenance"],
            "termina_listing_dispositions": recovered["termina_listing_dispositions"],
            "conclusion": recovered["conclusion"],
        }
    return {
        "generated_on": date.today().isoformat(),
        "database": str(database.relative_to(ROOT)),
        "claim_id": CLAIM_ID,
        "termina_status": claim["status"],
        "local_disposition": (
            "artifact-present-needs-row-reproduction"
            if all_held
            else "exact-claim-not-reproduced-independent-overlap-available"
            if independent
            else "not-independently-verifiable-from-held-files"
        ),
        "network_policy": "offline-only; no evidence URL, '+' statistics page, or redirect target is requested",
        "claim_text": claim["text"],
        "claim_notes": claim["notes"],
        "observations": {
            "before": "September 4 KMAD snapshots (exact timestamps and per-link counts unavailable here)",
            "after": "September 6 capture; primary evidence retrieved_at 2026-09-06T14:29:31.980509Z",
            "counter_semantics": "The claim notes describe YOURLS '+' statistics as a time series and state that viewing the listing does not increment the counter.",
        },
        "arithmetic": arithmetic,
        "artifacts": artifacts,
        "independent_comparison": independent,
        "limits": [
            "Neither declared upstream artifact is held at its referenced path in this repository unless supplied via --evidence-root.",
            "The aggregate partition 114 + 7 = 121 is internally consistent but does not reproduce eight per-link before/after differences.",
            "No-referrer does not identify a caller; investigators, bots, previews, and agents remain observationally compatible.",
            "The database claim and its evidence register share the Termina collector lineage, so they are not independent corroboration.",
        ],
    }


def markdown(report: dict) -> str:
    arithmetic = report["arithmetic"]
    artifacts = report["artifacts"]
    lines = [
        "# Vanderbilt post-publication click-delta audit",
        "",
        "**Disposition: the exact 121-click claim is not independently reproducible; a separate immutable overlap comparison is now available.** "
        "Both passes are offline and do not request a shortlink, `+` statistics page, or redirect target.",
        "",
        "## What can be checked",
        "",
        f"The claim describes **{arithmetic['links']} links** gaining **{arithmetic['delta']} clicks** between September 4 and September 6. "
        f"Its referrer partition is internally consistent: {arithmetic['no_referrer']} with no recorded referrer plus "
        f"{arithmetic['one_referrer']} with one equals {arithmetic['referrer_partition_total']}. "
        "This checks the prose arithmetic only; it does not reconstruct the eight individual counter differences.",
        "",
        "The pre-observation is identified only as the September 4 KMAD snapshots. The post-observation's registered primary evidence was retrieved "
        "at `2026-09-06T14:29:31.980509Z`. The claim notes say the YOURLS `+` page provides a time series and that viewing the listing does not increment "
        "the counter, but the underlying captures are needed to validate those counter values.",
        "",
        "## Artifact inventory",
        "",
        "| Evidence | Kind | Declared path | Present | Hash verified |",
        "|---|---|---|---|---|",
    ]
    for item in artifacts:
        lines.append(
            f"| `{item['evidence_id']}` | {item['kind']} | `{item['declared_path']}` | "
            f"{'yes' if item['present'] else 'no'} | {'yes' if item['hash_matches'] else 'no'} |"
        )
    independent = report.get("independent_comparison")
    if independent:
        summary = independent["summary"]
        lines += [
            "",
            "## Independent snapshot overlap",
            "",
            "A pinned KMAD checkout supplies 26 `+`-page captures from September 4 at about 22:09 UTC. "
            "A separate public repository supplies a Vanderbilt API export fetched September 5 at 12:20 UTC. "
            f"Of the 26 KMAD captures, **{summary['comparable_links']}** have parseable totals and matching API rows: "
            f"**{summary['increased_links']} increased**, **{summary['unchanged_links']} were unchanged**, and their aggregate increase was "
            f"**{summary['aggregate_delta']} clicks**. This is a different population and an earlier endpoint than Termina's selected-eight September 6 comparison, "
            "so it must not replace or be subtracted from 121.",
            "",
            "The local Termina venue row lists ten example statistics URLs. Their overlap dispositions are:",
            "",
            "| Alias | KMAD-to-public-export disposition | Observed delta |",
            "|---|---|---:|",
        ]
        for item in independent["termina_listing_dispositions"]:
            delta = "—" if item["observed_delta_to_public_api"] is None else str(item["observed_delta_to_public_api"])
            lines.append(f"| `{item['alias']}` | {item['kmad_lane5_disposition']} | {delta} |")
        lines += [
            "",
            "Six listed aliases are comparable and increased by 56 clicks in total; two listed aliases were unchanged; "
            "two have no capture in KMAD's committed lane-5 set. The claim does not identify which eight rows it selected. "
            "Therefore the exact eight-link sum and its 114/7 referrer partition remain unreproduced even though post-KMAD counter growth is independently demonstrated.",
            "",
            "The machine-readable recovery includes all 26 per-link dispositions and hashes for every KMAD HTML capture: "
            "[`data/vanderbilt_click_delta_recovery_2026-09-08.json`](../data/vanderbilt_click_delta_recovery_2026-09-08.json).",
        ]
    lines += [
        "",
        "## Bounded conclusion",
        "",
        "The pinned database supports reporting that Termina records a 121-click delta and supplies an internally consistent referrer partition. "
        "This repository cannot verify the selected-eight sum, its exact baselines, or the referrer partition because both named Termina artifacts are absent. "
        "The independent 25-link overlap proves substantial counter movement over a nearby interval, but does not identify Termina's selection. "
        "Even if the counter delta is correct, public statistics cannot distinguish agents from investigators, link previews, crawlers, or other visitors; "
        "the claim should not be used as an agent-activity signal.",
        "",
        "Machine-readable audit: "
        "[`data/termina_vanderbilt_click_audit_2026-09-08.json`](../data/termina_vanderbilt_click_audit_2026-09-08.json).",
        "",
        "Regenerate without network access:",
        "",
        "```sh",
        "python3 scripts/termina_vanderbilt_click_audit.py",
        "python3 scripts/vanderbilt_click_delta_recovery.py --kmad-root /path/to/agent-swarm-forensics --public-root /path/to/collusion-wiki-link-shorteners",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--evidence-root", type=Path, default=ROOT)
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "termina_vanderbilt_click_audit_2026-09-08.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "analysis" / "termina-vanderbilt-click-audit.md")
    args = parser.parse_args()
    report = build(args.database, args.evidence_root)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(report["local_disposition"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
