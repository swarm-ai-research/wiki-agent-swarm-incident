#!/usr/bin/env python3
"""Reproduce and bound Termina's cross-population IPv4 /16 mixture claim."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"


def ipv4_16(value: str) -> str:
    parts = value.strip().split(".")
    if len(parts) < 2:
        raise ValueError(f"not an IPv4 prefix or address: {value!r}")
    try:
        first, second = int(parts[0]), int(parts[1])
        ipaddress.IPv4Address(f"{first}.{second}.0.0")
    except (ValueError, ipaddress.AddressValueError) as error:
        raise ValueError(f"not an IPv4 prefix or address: {value!r}") from error
    return f"{first}.{second}"


def category(a_count: int, b_count: int, threshold: int = 3) -> str:
    if a_count >= threshold * b_count:
        return "a-talk-leaning"
    if b_count >= threshold * a_count:
        return "b-tools-leaning"
    return "balanced-mixed"


def build(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        start = connection.execute(
            "SELECT first_action FROM incident WHERE id='dsewiki-2026-05'"
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT r.population_id, a.name AS prefix
            FROM record r JOIN actor a ON a.id = r.ip_actor_id
            WHERE a.kind='ip16' AND r.population_id IN ('a-talk','b-tools')
            """
        )
        populations = defaultdict(Counter)
        for row in rows:
            populations[ipv4_16(row["prefix"])][row["population_id"]] += 1

        rmn_rows = list(connection.execute(
            """
            SELECT r.id, a.name AS address
            FROM record r JOIN actor a ON a.id = r.ip_actor_id
            WHERE r.venue_id='rmn-re' AND r.observed_time >= ? AND a.kind='ip'
            """,
            (start,),
        ))
    finally:
        connection.close()

    prefix_rows = []
    for prefix, counts in sorted(populations.items()):
        a_count, b_count = counts["a-talk"], counts["b-tools"]
        prefix_rows.append({
            "prefix": prefix,
            "a_talk_records": a_count,
            "b_tools_records": b_count,
            "both_populations": bool(a_count and b_count),
            "category_3_to_1": category(a_count, b_count),
        })
    a_prefixes = {row["prefix"] for row in prefix_rows if row["a_talk_records"]}
    b_prefixes = {row["prefix"] for row in prefix_rows if row["b_tools_records"]}
    shared = a_prefixes & b_prefixes

    rmn_counts = Counter()
    rmn_unique = defaultdict(set)
    for row in rmn_rows:
        try:
            prefix = ipv4_16(row["address"])
        except ValueError:
            classification = "invalid-or-redacted"
        else:
            classification = next(
                (item["category_3_to_1"] for item in prefix_rows if item["prefix"] == prefix),
                "unmatched",
            )
        rmn_counts[classification] += 1
        rmn_unique[classification].add(row["address"])

    sensitivity = {}
    for threshold in (2, 3, 5, 10):
        sensitivity[str(threshold)] = dict(sorted(Counter(
            category(row["a_talk_records"], row["b_tools_records"], threshold)
            for row in prefix_rows
        ).items()))

    categories = Counter(row["category_3_to_1"] for row in prefix_rows)
    actual_both = sum(row["both_populations"] for row in prefix_rows)
    return {
        "generated_on": date.today().isoformat(),
        "database": str(database.relative_to(ROOT)),
        "claim_id": "networks-do-not-separate-populations",
        "evidence_status": "inferred",
        "method": {
            "population_input": "all records with population_id a-talk or b-tools and an ip16 actor",
            "prefix_normalization": "first two validated IPv4 octets; masked trailing octets are ignored",
            "leaning_rule": "record count for one population is at least threshold times the other; zero counts are included",
            "rmn_window": f"observed_time >= incident.first_action ({start})",
            "important_limit": "The pinned database and its declared rmn.re evidence are one synthesis lineage; this is arithmetic reproduction, not independent collection.",
        },
        "wiki_prefixes": {
            "union": len(a_prefixes | b_prefixes),
            "a_talk": len(a_prefixes),
            "b_tools": len(b_prefixes),
            "both_any_ratio": actual_both,
            "jaccard": round(len(shared) / len(a_prefixes | b_prefixes), 4),
            "records_on_shared_prefixes": sum(
                row["a_talk_records"] + row["b_tools_records"]
                for row in prefix_rows if row["both_populations"]
            ),
            "labeled_records": sum(row["a_talk_records"] + row["b_tools_records"] for row in prefix_rows),
            "categories_3_to_1": dict(sorted(categories.items())),
            "sensitivity": sensitivity,
        },
        "rmn_re": {
            "records_in_window": len(rmn_rows),
            "by_prefix_category": dict(sorted(rmn_counts.items())),
            "unique_masked_ips_by_category": {key: len(value) for key, value in sorted(rmn_unique.items())},
        },
        "terminology_correction": {
            "claim_says_both": 131,
            "actual_prefixes_with_both": actual_both,
            "balanced_mixed_at_3_to_1": categories["balanced-mixed"],
            "explanation": "131 is the balanced remainder after the two leaning categories; 137 prefixes actually contain records from both populations.",
        },
        "prefixes": prefix_rows,
    }


def markdown(report: dict) -> str:
    wiki, rmn, correction = report["wiki_prefixes"], report["rmn_re"], report["terminology_correction"]
    cats = wiki["categories_3_to_1"]
    rmn_cats = rmn["by_prefix_category"]
    lines = [
        "# Termina cross-network `/16` mixture reproduction",
        "",
        "**Disposition: arithmetic reproduced, wording corrected, inference not independently verified.** "
        "The computation uses the pinned [Termina schema-v9 database](../data/termina/README.md), "
        "which is the same synthesis lineage named by the claim.",
        "",
        "## Reproduced result",
        "",
        f"The labeled wiki records occupy **{wiki['union']} IPv4 `/16` prefixes**: "
        f"{wiki['a_talk']} carry `a-talk`, {wiki['b_tools']} carry `b-tools`, and "
        f"**{wiki['both_any_ratio']} actually carry both** (Jaccard {wiki['jaccard']:.4f}). "
        f"Shared prefixes contain {wiki['records_on_shared_prefixes']:,} of {wiki['labeled_records']:,} labeled records.",
        "",
        f"At the stated 3:1 threshold the mutually exclusive categories are **{cats['balanced-mixed']} balanced mixed**, "
        f"**{cats['a-talk-leaning']} a-talk-leaning**, and **{cats['b-tools-leaning']} b-tools-leaning**. "
        "The claim's `131 serve both` phrase is imprecise: 131 is the balanced remainder, while 137 prefixes contain both populations. "
        "The leaning categories include prefixes observed for only one population because the comparison count is zero.",
        "",
        "## `rmn.re` join",
        "",
        f"Using `observed_time >= 2026-05-11`, the incident's recorded first action, reproduces **{rmn['records_in_window']} shortlinks**. "
        f"Their creating-IP prefixes map to {rmn_cats.get('balanced-mixed', 0)} balanced mixed, "
        f"{rmn_cats.get('a-talk-leaning', 0)} a-talk-leaning, and {rmn_cats.get('b-tools-leaning', 0)} b-tools-leaning records. "
        f"A further **{rmn_cats.get('unmatched', 0)}** have `/16`s absent from the labeled wiki prefix set and "
        f"**{rmn_cats.get('invalid-or-redacted', 0)}** has no usable address (`*`); the original prose omits both categories.",
        "",
        "## Threshold sensitivity",
        "",
        "| Dominance threshold | Balanced mixed | a-talk-leaning | b-tools-leaning |",
        "|---:|---:|---:|---:|",
    ]
    for threshold, values in wiki["sensitivity"].items():
        lines.append(
            f"| {threshold}:1 | {values.get('balanced-mixed', 0)} | "
            f"{values.get('a-talk-leaning', 0)} | {values.get('b-tools-leaning', 0)} |"
        )
    lines += [
        "",
        "## Bounded conclusion",
        "",
        "The high prefix overlap supports the narrow statement that `/16` membership does not cleanly partition the two style-defined populations in this dataset. "
        "It does **not** establish a common operator or a single deployment: `/16` aggregation can combine unrelated tenants, NAT or cloud egress, and the population labels themselves are style-derived. "
        "No ASN, tenant, or independently collected network evidence is present in this calculation.",
        "",
        "Machine-readable counts and the complete prefix table: "
        "[`data/termina_network_mixture_2026-09-08.json`](../data/termina_network_mixture_2026-09-08.json).",
        "",
        "Regenerate with:",
        "",
        "```sh",
        "python3 scripts/termina_network_mixture.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "termina_network_mixture_2026-09-08.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "analysis" / "termina-network-mixture.md")
    args = parser.parse_args()
    report = build(args.database)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(f"classified {report['wiki_prefixes']['union']} wiki /16s and {report['rmn_re']['records_in_window']} rmn.re rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
