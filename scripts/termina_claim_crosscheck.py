#!/usr/bin/env python3
"""Build a status-preserving cross-check of Termina claims relevant to DSEWiki."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"

CORROBORATED = {
    "dse-talk-arrives-with-the-clock": "analysis/spec-emergence.md",
    "ihme-cvd-mcv2-sequences": "analysis/sub-swarms.md",
    "ihme-family-planning-cohort-markers": "analysis/sub-swarms.md",
    "oecd-equity-cohort-markers": "analysis/sub-swarms.md",
    "openai-acknowledges-wiki-incident": "sources.md",
    "ours-persistence": "analysis/replay-findings.md",
    "ours-probier-payload-is-data": "analysis/field-evidence.md",
    "ours-h4si-urlsafe": "analysis/field-evidence.md",
    "centaur-tagblock": "analysis/field-evidence.md",
    "visitors-dse": "analysis/timeline.md",
    "visitors-fractal": "analysis/timeline.md",
    "visitors-linuxiarz": "analysis/wayback-cdx-sweep.md",
    "rmn-re-june-burst": "analysis/rmn-re-verify.md",
}

CONFLICTING = {
    "kmad-probier-negative": "analysis/field-evidence.md",
}

NEW_SUBSTANTIVE = {
    "retrieval-venues-never-talk": "The archive has not reproduced the 870-body content classification.",
    "rmn-re-shares-wiki-networks": "The archive confirms an Azure-heavy shortener listing but has not reproduced the exact join to wiki /16s.",
}

SAME_LINEAGE_REPRODUCED = {
    "networks-do-not-separate-populations": (
        "analysis/termina-network-mixture.md",
        "The arithmetic is reproduced from the pinned Termina rows, not independent evidence. "
        "The audit corrects '131 serve both' to 137 and accounts for 49 rmn.re rows omitted from the prose breakdown.",
    ),
}

BOUNDED_UNVERIFIED = {
    "kmad-wiki-sweep": (
        "analysis/termina-kmad-sweep-audit.md",
        "The immutable KMAD repository repeats the 6,271-candidate result in three narrative files but publishes no target list, response ledger, sweep code, retry log, or machine-readable result.",
    ),
    "vanderbilt-post-publication-clicks": (
        "analysis/termina-vanderbilt-click-audit.md",
        "The 114 + 7 = 121 prose partition is internally consistent, but both named upstream captures are absent locally, so the eight per-link deltas remain unverified.",
    ),
}

LOCAL_NOTES = {
    "rmn-re-june-burst": "Partially corroborated: the archive independently reproduces 484 June rows and the host pattern, but uses different snapshot/counting cuts for other figures.",
    "kmad-probier-negative": "The claim is already marked contradicted by Termina. The archive independently decodes the four-page payload to a 968-row IPEDS table.",
}

SCAN_RE = re.compile(r"^scan:(?P<venue>[^:]+):(?P<day>\d{4}-\d{2}-\d{2})$")
VERDICT_RE = re.compile(r"scanner verdict (?P<verdict>.+?) on \d+ rows")


def _rows(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    sql = """
    WITH dse_subjects(kind, id) AS (
      VALUES ('incident', 'dsewiki-2026-05')
      UNION SELECT 'campaign', id FROM campaign
        WHERE id IN ('swarm-cohort', 'swarm-retrieval')
      UNION SELECT 'cluster', id FROM cluster
        WHERE campaign_id IN ('swarm-cohort', 'swarm-retrieval')
      UNION SELECT 'venue', venue_id FROM record
        WHERE incident_id = 'dsewiki-2026-05'
      UNION SELECT 'record', id FROM record
        WHERE incident_id = 'dsewiki-2026-05'
    )
    SELECT claim.*
    FROM claim JOIN dse_subjects
      ON dse_subjects.kind = claim.subject_kind
     AND dse_subjects.id = claim.subject_id
    ORDER BY claim.subject_kind, claim.subject_id, claim.id
    """
    return [dict(row) for row in connection.execute(sql)]


def _evidence(connection: sqlite3.Connection) -> dict[str, dict]:
    connection.row_factory = sqlite3.Row
    return {row["id"]: dict(row) for row in connection.execute("SELECT * FROM evidence")}


def _scan_transitions(claims: list[dict]) -> set[str]:
    by_venue: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for claim in claims:
        match = SCAN_RE.match(claim["id"])
        verdict = VERDICT_RE.search(claim["text"])
        if match and verdict:
            by_venue[match["venue"]].append(
                (match["day"], claim["id"], verdict["verdict"])
            )
    changed = set()
    for snapshots in by_venue.values():
        snapshots.sort()
        for previous, current in zip(snapshots, snapshots[1:]):
            if previous[2] != current[2]:
                changed.add(current[1])
    return changed


def classify(claim: dict, changed_scans: set[str]) -> tuple[str, bool, str, str]:
    claim_id = claim["id"]
    if claim_id in CONFLICTING:
        return "conflicting", True, CONFLICTING[claim_id], LOCAL_NOTES[claim_id]
    if claim_id in CORROBORATED:
        note = LOCAL_NOTES.get(
            claim_id,
            "The archive independently records the same underlying artifact or observation.",
        )
        return "corroborated", True, CORROBORATED[claim_id], note
    if claim_id in SAME_LINEAGE_REPRODUCED:
        archive_ref, note = SAME_LINEAGE_REPRODUCED[claim_id]
        return "repeated", True, archive_ref, note
    if claim_id in BOUNDED_UNVERIFIED:
        archive_ref, note = BOUNDED_UNVERIFIED[claim_id]
        return "new", True, archive_ref, note
    if claim_id in NEW_SUBSTANTIVE:
        return "new", True, "", NEW_SUBSTANTIVE[claim_id]
    scan = SCAN_RE.match(claim_id)
    if scan:
        is_new = scan["day"] > "2026-09-06"
        material = claim_id in changed_scans
        note = "Later daily scanner snapshot; not independent evidence."
        if material:
            note += " Its verdict changed from the preceding snapshot."
        return ("new" if is_new else "repeated"), material, "sources.md", note
    return (
        "repeated",
        False,
        "sources.md",
        "Already represented in the archive or derived from the same evidence lineage; not independent corroboration.",
    )


def build(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        claims = _rows(connection)
        evidence = _evidence(connection)
        changed_scans = _scan_transitions(claims)
        audited = []
        for claim in claims:
            classification, material, archive_ref, note = classify(claim, changed_scans)
            item = dict(claim)
            item.update(
                classification=classification,
                material=material,
                archive_ref=archive_ref,
                audit_note=note,
                made_by_evidence=evidence.get(claim.get("made_by")),
                checked_by_evidence=evidence.get(claim.get("checked_by")),
            )
            audited.append(item)
    finally:
        connection.close()

    return {
        "generated_on": date.today().isoformat(),
        "database": str(database.relative_to(ROOT)),
        "scope": {
            "incident": "dsewiki-2026-05",
            "campaigns": ["swarm-cohort", "swarm-retrieval"],
            "rule": "incident plus its campaigns, their clusters, incident venues, and incident records",
        },
        "counts": {
            "claims": len(audited),
            "by_termina_status": dict(sorted(Counter(c["status"] for c in audited).items())),
            "by_classification": dict(sorted(Counter(c["classification"] for c in audited).items())),
            "material": sum(c["material"] for c in audited),
        },
        "claims": audited,
    }


def markdown(report: dict) -> str:
    counts = report["counts"]
    material = [claim for claim in report["claims"] if claim["material"]]
    lines = [
        "# Termina claim cross-check",
        "",
        "**Source:** the pinned [`swarm.termina.digital` schema-v9 database](../data/termina/README.md). "
        "This is a row-complete audit of claims attached to `dsewiki-2026-05`, its two campaigns, "
        "their clusters, and venues or records assigned to the incident.",
        "",
        "Termina is a structured secondary synthesis. A matching statement is called **corroborated** "
        "only when this archive independently checked the underlying artifact; **repeated** means the "
        "claim was already represented or shares its evidence lineage; **new** means absent here, not "
        "proven; **conflicting** means direct held/read evidence disagrees.",
        "",
        "## Result",
        "",
        f"The scope contains **{counts['claims']} claims**. Termina statuses: "
        + ", ".join(f"{value} {key}" for key, value in counts["by_termina_status"].items())
        + ". Cross-check classes: "
        + ", ".join(f"{value} {key}" for key, value in counts["by_classification"].items())
        + f". **{counts['material']} rows require attention**; the rest are lineage repeats or unchanged scanner snapshots.",
        "",
        "## Material deltas",
        "",
        "| Claim | Termina status | Cross-check | Archive reference | Disposition |",
        "|---|---|---|---|---|",
    ]
    for claim in material:
        ref = f"[`{claim['archive_ref']}`](../{claim['archive_ref']})" if claim["archive_ref"] else "—"
        lines.append(
            f"| `{claim['id']}` | {claim['status']} | {claim['classification']} | {ref} | {claim['audit_note']} |"
        )
    lines += [
        "",
        "## Reading the daily scans",
        "",
        "Daily `scan:*` claims are measurements made by Termina's scanner, not independent witnesses. "
        "They are retained row by row below because the input changed between regenerations. Only a "
        "verdict transition is marked material; row-count drift alone is not promoted to a finding.",
        "",
        "## Complete claim register",
        "",
        "| ID | Subject | Status | Class | Material |",
        "|---|---|---|---|---|",
    ]
    for claim in report["claims"]:
        lines.append(
            f"| `{claim['id']}` | `{claim['subject_kind']}:{claim['subject_id']}` | "
            f"{claim['status']} | {claim['classification']} | {'yes' if claim['material'] else 'no'} |"
        )
    lines += [
        "",
        "Machine-readable rows, including the claim text and resolved `made_by` / `checked_by` evidence "
        "objects, are in [`data/termina_claim_crosscheck_2026-09-08.json`](../data/termina_claim_crosscheck_2026-09-08.json).",
        "",
        "Regenerate with:",
        "",
        "```sh",
        "python3 scripts/termina_claim_crosscheck.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument(
        "--json", type=Path, default=ROOT / "data" / "termina_claim_crosscheck_2026-09-08.json"
    )
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "analysis" / "termina-crosscheck.md"
    )
    args = parser.parse_args()
    report = build(args.database)
    args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
