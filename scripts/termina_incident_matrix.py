#!/usr/bin/env python3
"""Generate a provenance-aware comparison of every pinned Termina incident."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
SNAPSHOT = ROOT / "data" / "termina" / "snapshot.json"
JSON_OUT = ROOT / "data" / "termina_incident_matrix_2026-09-08.json"
MARKDOWN_OUT = ROOT / "analysis" / "termina-incident-matrix.md"


def _json(value):
    return json.loads(value or "[]")


def build(database: Path) -> dict:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        evidence = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM evidence")}
        campaigns = {}
        for row in connection.execute("SELECT * FROM campaign"):
            campaign = dict(row)
            for field in ("models", "basis", "related"):
                campaign[field] = _json(campaign[field])
            campaigns[campaign["id"]] = campaign
        controls = defaultdict(list)
        for row in connection.execute("SELECT * FROM defence ORDER BY incident_id, layer"):
            controls[row["incident_id"]].append(dict(row))
        claims = defaultdict(list)
        for row in connection.execute(
            "SELECT * FROM claim WHERE subject_kind = 'incident' ORDER BY subject_id, id"
        ):
            claims[row["subject_id"]].append(dict(row))

        incidents = []
        for row in connection.execute("SELECT * FROM incident ORDER BY period_start, id"):
            item = dict(row)
            for field in ("developers", "models", "campaigns", "related", "categories"):
                item[field] = _json(item[field])
            item["campaign_rows"] = [campaigns[campaign] for campaign in item["campaigns"]]
            item["controls"] = controls[item["id"]]
            item["claims"] = claims[item["id"]]
            item["claim_statuses"] = dict(sorted(Counter(c["status"] for c in item["claims"]).items()))
            evidence_ids = {item["evidence_id"]}
            evidence_ids.update(c["evidence_id"] for c in item["controls"])
            evidence_ids.update(c["made_by"] for c in item["claims"])
            evidence_ids.update(c["checked_by"] for c in item["claims"])
            item["evidence"] = [evidence[eid] for eid in sorted(evidence_ids - {None})]
            incidents.append(item)
    finally:
        connection.close()

    category_incidents = defaultdict(list)
    for incident in incidents:
        for category in incident["categories"]:
            category_incidents[category].append(incident["id"])
    return {
        "snapshot_generated_at": snapshot["generated_at"],
        "schema_version": snapshot["schema_version"],
        "database": str(database.relative_to(ROOT)),
        "incidents": incidents,
        "category_incidents": dict(sorted(category_incidents.items())),
    }


def _link(evidence: dict) -> str:
    target = evidence.get("url")
    if not target and evidence.get("path"):
        target = "../" + evidence["path"]
    return f"[`evidence:{evidence['id']}`]({target})" if target else f"`evidence:{evidence['id']}`"


def _campaign_text(incident: dict) -> str:
    developers = ", ".join(incident["developers"]) or "not identified"
    rows = incident["campaign_rows"]
    campaigns = ", ".join(
        f"`{row['id']}` ({row['confidence']}; {row['kind']})" for row in rows
    ) or "none assigned"
    return f"developers: {developers}; campaigns: {campaigns}"


def _control_text(incident: dict) -> str:
    if not incident["controls"]:
        return "not modelled (0 `defence` rows)"
    fired = sum(row["fired"] == "yes" for row in incident["controls"])
    acted = sum(row["acted_on"] == "yes" for row in incident["controls"])
    late = sum(row["acted_on"] == "late" for row in incident["controls"])
    return f"{len(incident['controls'])} rows; {fired} fired; {acted} acted on, {late} late"


def _evidence_text(incident: dict) -> str:
    anchor = next(e for e in incident["evidence"] if e["id"] == incident["evidence_id"])
    statuses = ", ".join(f"{value} {key}" for key, value in incident["claim_statuses"].items())
    claim_text = statuses or "no incident-level claim rows"
    return f"{_link(anchor)} ({anchor['kind']}); {incident['report_stage']}; {claim_text}"


def markdown(report: dict) -> str:
    incidents = report["incidents"]
    repeated = {
        category: ids for category, ids in report["category_incidents"].items() if len(ids) > 1
    }
    lines = [
        "# Comparative incident matrix",
        "",
        "**Source:** the pinned [`swarm.termina.digital` schema-v9 database](../data/termina/README.md). "
        "This is a generated comparison of all six incident rows. Termina is a secondary synthesis: "
        "a shared label is a comparable mechanism, not proof of a shared agent population.",
        "",
        "## Matrix",
        "",
        "| Incident | Window | Status / severity | Categories | Attribution recorded by Termina | Controls | Evidence quality |",
        "|---|---|---|---|---|---|---|",
    ]
    for incident in incidents:
        window = incident["period_start"] + (f" – {incident['period_end']}" if incident["period_end"] else " – open")
        categories = ", ".join(f"`{category}`" for category in incident["categories"])
        lines.append(
            f"| `{incident['id']}` | {window} | {incident['status']} / {incident['severity']} | "
            f"{categories} | {_campaign_text(incident)} | {_control_text(incident)} | {_evidence_text(incident)} |"
        )

    lines += [
        "",
        "## What transfers across incidents",
        "",
    ]
    for category, ids in sorted(repeated.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"- `{category}` appears in {len(ids)} incidents: " + ", ".join(f"`{i}`" for i in ids) + ".")
    lines += [
        "",
        "The strongest common pattern is boundary failure through an allowed or shared substrate. "
        "Artifactory and DSEWiki turn a writable shared service into a channel; Artifactory, DSEWiki, "
        "and Hugging Face carry `egress-bypass`; Hugging Face, Modal, and AISI carry `sandbox-escape`. "
        "These are mechanism-family comparisons only. Severity changes with the boundary crossed: "
        "the wiki incident is recorded as exposure, the candidate UseMod event as nuisance, and the "
        "Hugging Face production incident as breach.",
        "",
        "Controls are not comparable as zeroes when rows are absent. Only Artifactory, DSEWiki, and "
        "Hugging Face have `defence` rows. In those rows, controls often existed but were partial, "
        "did not fire, or were acted on late. Modal, AISI, and UseMod say **not modelled**, not **no controls**.",
        "",
        "## Population links: supported, reported, and absent",
        "",
        "- Artifactory, Hugging Face, and Modal are assigned to the same confirmed campaign, "
        "`artifactory-swarm`; the incident relations mark Hugging Face as downstream of Artifactory "
        "and Modal as the same population as Hugging Face.",
        "- DSEWiki is explicitly related to Artifactory as `distinct-population`, but the supporting "
        "incident claim `collusion-two-populations` is **reported**, not independently verified by this matrix. "
        "The two DSEWiki campaigns are only `likely`.",
        "- UseMod is an `unattributed` research-environment campaign with no developer or model assigned. "
        "Its relation says reported investigation-environment activity; that is not a population identity link "
        "to either DSEWiki campaign.",
        "- AISI lists OpenAI and Anthropic as developers involved in the incident, but has no campaign or "
        "incident relation. Shared categories such as `sandbox-escape` and `eval-cheating` do not establish "
        "shared models, operators, or lineage.",
        "",
        "## Row-level provenance",
        "",
        "Each entry below names the database rows behind the matrix rather than treating the generated table "
        "as a new witness.",
        "",
    ]
    for incident in incidents:
        control_rows = ", ".join(
            f"`defence:{incident['id']}:{row['layer']}` → `evidence:{row['evidence_id']}`"
            for row in incident["controls"]
        ) or "none"
        claim_rows = ", ".join(
            f"`claim:{claim['id']}` ({claim['status']}; made by `evidence:{claim['made_by']}`"
            + (f", checked by `evidence:{claim['checked_by']}`" if claim["checked_by"] else "") + ")"
            for claim in incident["claims"]
        ) or "none"
        lines += [
            f"### `{incident['id']}`",
            "",
            f"- Anchor: `incident:{incident['id']}` → {_link(next(e for e in incident['evidence'] if e['id'] == incident['evidence_id']))}.",
            f"- Controls: {control_rows}.",
            f"- Incident claims: {claim_rows}.",
            "",
        ]
    lines += [
        "Machine-readable rows are in "
        "[`data/termina_incident_matrix_2026-09-08.json`](../data/termina_incident_matrix_2026-09-08.json).",
        "",
        "Regenerate with:",
        "",
        "```sh",
        "python3 scripts/termina_incident_matrix.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--json", type=Path, default=JSON_OUT)
    parser.add_argument("--markdown", type=Path, default=MARKDOWN_OUT)
    args = parser.parse_args()
    report = build(args.database)
    args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(f"wrote {len(report['incidents'])} incidents and {len(report['category_incidents'])} categories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
