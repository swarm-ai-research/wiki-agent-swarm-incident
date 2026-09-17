#!/usr/bin/env python3
"""Audit claim/evidence lineage in the pinned Termina incident database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
NON_PRIMARY = {"agent-authored", "report", "secondary"}


def canonical_publisher(value: str | None) -> str:
    """Normalize only explicitly equivalent publisher labels."""
    value = (value or "").strip().casefold()
    aliases = {
        "ai-safety-lab (live pull)": "ai-safety-lab",
    }
    return aliases.get(value, value)


def check_class(maker: dict, checker: dict | None) -> str:
    if checker is None:
        return "unchecked"
    if maker["id"] == checker["id"]:
        return "same-evidence"
    if canonical_publisher(maker["publisher"]) == canonical_publisher(
        checker["publisher"]
    ):
        return "same-publisher"
    if maker["collection_id"] and maker["collection_id"] == checker["collection_id"]:
        return "same-collection"
    return "cross-publisher"


def local_path_state(path: str | None) -> str:
    if not path:
        return "not-declared"
    return "present" if (ROOT / path).exists() else "absent"


def build(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        evidence = {row["id"]: dict(row) for row in connection.execute(
            "SELECT * FROM evidence ORDER BY id"
        )}
        claims = [dict(row) for row in connection.execute("SELECT * FROM claim ORDER BY id")]
    finally:
        connection.close()

    evidence_rows = []
    used_as_maker = Counter(c["made_by"] for c in claims if c["made_by"])
    used_as_checker = Counter(c["checked_by"] for c in claims if c["checked_by"])
    for row in evidence.values():
        item = dict(row)
        item.update(
            canonical_publisher=canonical_publisher(row["publisher"]),
            local_path_state=local_path_state(row["path"]),
            maker_claims=used_as_maker[row["id"]],
            checker_claims=used_as_checker[row["id"]],
        )
        evidence_rows.append(item)

    audited = []
    for claim in claims:
        maker = evidence.get(claim["made_by"])
        checker = evidence.get(claim["checked_by"])
        if maker is None:
            raise ValueError(f"claim {claim['id']} has unresolved made_by={claim['made_by']!r}")
        classification = check_class(maker, checker)
        flags = []
        if checker is None:
            flags.append("unchecked")
        elif classification in {"same-evidence", "same-publisher", "same-collection"}:
            flags.append("non-independent-check")
        else:
            flags.append("publisher-distinct-check; independence-not-established")
        if maker["kind"] in NON_PRIMARY and checker and checker["kind"] in NON_PRIMARY:
            flags.append("non-primary-on-non-primary")
        if local_path_state(maker["path"]) == "absent":
            flags.append("maker-path-not-in-this-repository")
        if checker and local_path_state(checker["path"]) == "absent":
            flags.append("checker-path-not-in-this-repository")
        audited.append(
            {
                **claim,
                "check_class": classification,
                "flags": flags,
                "made_by_evidence": maker,
                "checked_by_evidence": checker,
            }
        )

    lineage_edges = []
    for claim in audited:
        lineage_edges.append(
            {
                "claim_id": claim["id"],
                "evidence_id": claim["made_by"],
                "role": "made_by",
                "evidence_kind": claim["made_by_evidence"]["kind"],
                "independence": "assertion-source",
            }
        )
        if claim["checked_by_evidence"]:
            lineage_edges.append(
                {
                    "claim_id": claim["id"],
                    "evidence_id": claim["checked_by"],
                    "role": "checked_by",
                    "evidence_kind": claim["checked_by_evidence"]["kind"],
                    "independence": claim["check_class"],
                }
            )

    return {
        "generated_on": date.today().isoformat(),
        "database": str(database.relative_to(ROOT)),
        "method": {
            "scope": "all claim and evidence rows in the pinned database",
            "independence_rule": "Publisher distinction is a screening signal, not proof of independent collection.",
            "local_path_rule": "Evidence paths are upstream-relative; present means an identically named path exists in this repository.",
        },
        "counts": {
            "claims": len(audited),
            "evidence": len(evidence_rows),
            "lineage_edges": len(lineage_edges),
            "by_claim_status": dict(sorted(Counter(c["status"] for c in audited).items())),
            "by_evidence_kind": dict(sorted(Counter(e["kind"] for e in evidence_rows).items())),
            "by_check_class": dict(sorted(Counter(c["check_class"] for c in audited).items())),
            "non_primary_on_non_primary": sum("non-primary-on-non-primary" in c["flags"] for c in audited),
            "unused_evidence": sum(not e["maker_claims"] and not e["checker_claims"] for e in evidence_rows),
            "local_path_state": dict(sorted(Counter(e["local_path_state"] for e in evidence_rows).items())),
        },
        "claims": audited,
        "evidence": evidence_rows,
        "lineage_edges": lineage_edges,
    }


def markdown(report: dict) -> str:
    counts = report["counts"]
    flagged = [c for c in report["claims"] if c["check_class"] != "cross-publisher"]
    non_primary = [c for c in report["claims"] if "non-primary-on-non-primary" in c["flags"]]
    lines = [
        "# Termina evidence-lineage audit",
        "",
        "**Scope:** every claim and evidence row in the pinned [schema-v9 snapshot](../data/termina/README.md). "
        "This audit measures declared lineage; it does not turn a distinct publisher into proof of independent collection.",
        "",
        "## Result",
        "",
        f"The database contains **{counts['claims']} claims**, **{counts['evidence']} evidence rows**, "
        f"and **{counts['lineage_edges']} declared lineage edges**. "
        + "Declared checking: "
        + ", ".join(f"{value} {key}" for key, value in counts["by_check_class"].items())
        + f". There are **{counts['non_primary_on_non_primary']} non-primary-on-non-primary claims** and "
        + f"**{counts['unused_evidence']} evidence rows unused by a claim**.",
        "",
        "`same-evidence`, `same-publisher`, and `same-collection` are non-independent checks. "
        "`cross-publisher` means only that publisher labels differ; shared inputs or copying may still exist. "
        "`unchecked` means the row has no declared `checked_by` evidence.",
        "",
        "## Non-independent or absent checks",
        "",
        "| Claim | Status | Made by | Checked by | Classification |",
        "|---|---|---|---|---|",
    ]
    for claim in flagged:
        checker = claim["checked_by"] or "—"
        lines.append(
            f"| `{claim['id']}` | {claim['status']} | `{claim['made_by']}` | "
            f"`{checker}` | {claim['check_class']} |"
        )
    lines += [
        "",
        "## Non-primary-on-non-primary support",
        "",
        "These claims are made and checked by some combination of `report`, `secondary`, or "
        "`agent-authored` evidence. This is a review queue, not a finding that the claim is false.",
        "",
        "| Claim | Maker kind | Checker kind | Check class |",
        "|---|---|---|---|",
    ]
    for claim in non_primary:
        lines.append(
            f"| `{claim['id']}` | {claim['made_by_evidence']['kind']} | "
            f"{claim['checked_by_evidence']['kind']} | {claim['check_class']} |"
        )
    lines += [
        "",
        "## Path availability",
        "",
        "Termina's `evidence.path` values name files in the upstream collector tree. They are not "
        "promises that this repository holds those files. Current states: "
        + ", ".join(f"{value} {key}" for key, value in counts["local_path_state"].items())
        + ".",
        "",
        "The complete claim-to-evidence register and evidence inventory are in "
        "[`data/termina_evidence_lineage_2026-09-08.json`](../data/termina_evidence_lineage_2026-09-08.json).",
        "",
        "Regenerate with:",
        "",
        "```sh",
        "python3 scripts/termina_evidence_lineage.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "termina_evidence_lineage_2026-09-08.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "analysis" / "termina-evidence-lineage.md")
    args = parser.parse_args()
    report = build(args.database)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown.write_text(markdown(report))
    print(f"audited {report['counts']['claims']} claims and {report['counts']['evidence']} evidence rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
