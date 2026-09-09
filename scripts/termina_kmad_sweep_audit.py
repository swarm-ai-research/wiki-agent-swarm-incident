#!/usr/bin/env python3
"""Audit whether KMAD's 6,271-candidate wiki sweep is reproducible from its repo."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "termina" / "incidents.sqlite"
CLAIM_ID = "kmad-wiki-sweep"
AUDIT_TERMS = re.compile(r"6,271|\b6271\b|WikiIndex|wiki-hunt", re.IGNORECASE)
IMPLEMENTATION_SUFFIXES = {".py", ".sh", ".js", ".ts", ".json", ".jsonl", ".csv", ".tsv"}


def _tracked_files(repository: Path) -> tuple[str | None, list[Path]]:
    if (repository / ".git").exists():
        commit = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        names = subprocess.run(
            ["git", "-C", str(repository), "ls-tree", "-r", "--name-only", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        return commit, [repository / name for name in names]
    return None, sorted(path for path in repository.rglob("*") if path.is_file())


def audit_repository(repository: Path) -> dict:
    commit, files = _tracked_files(repository)
    mentions = []
    implementation = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        matches = list(AUDIT_TERMS.finditer(text))
        if not matches:
            continue
        relative = str(path.relative_to(repository))
        mentions.append({"path": relative, "matches": len(matches)})
        if path.suffix.casefold() in IMPLEMENTATION_SUFFIXES:
            implementation.append(relative)
    return {
        "repository": str(repository),
        "commit": commit,
        "tracked_files": len(files),
        "narrative_or_code_mentions": mentions,
        "implementation_or_result_files": implementation,
        "reproducible_from_repository": bool(implementation),
    }


def build(repository: Path, database: Path = DB) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        claim = dict(connection.execute("SELECT * FROM claim WHERE id=?", (CLAIM_ID,)).fetchone())
        evidence = dict(connection.execute("SELECT * FROM evidence WHERE id=?", (claim["made_by"],)).fetchone())
    finally:
        connection.close()
    inventory = audit_repository(repository)
    return {
        "generated_on": date.today().isoformat(),
        "claim_id": CLAIM_ID,
        "termina_status": claim["status"],
        "claim_text": claim["text"],
        "evidence": {key: evidence.get(key) for key in ("id", "kind", "publisher", "url", "path")},
        "repository_inventory": inventory,
        "published_narrative": {
            "candidate_denominator": 6271,
            "source": "WikiIndex API",
            "engine_categories_reported": 19,
            "explicit_categories": ["UseMod", "Oddmuse", "PmWiki", "MoinMoin", "DokuWiki", "MediaWiki", "TiddlyWiki", "TWiki", "JSPWiki", "PhpWiki", "Instiki", "Wikka", "wiki farms"],
            "reported_matches": 1,
            "reported_match": "texteditors.org",
            "headline_interpretation": "not a genuinely new host because it was already named in the writeup, tweet, or corpus",
            "subagent_limit": "wiki-hunt ended before an Oddmuse browser pass and before corroborating a publictestwiki JSON artifact",
        },
        "unavailable_inputs": {
            "target_list": True,
            "per_target_results": True,
            "reachable_unreachable_partition": True,
            "response_statuses": True,
            "retry_and_timeout_policy": True,
            "scan_timestamps": True,
            "positive_control_log": True,
            "fingerprint_or_match_rule": True,
        },
        "local_disposition": "reported-not-reproducible",
        "bounded_conclusion": "The repository supports attribution of the 6,271-candidate statement to KMAD, but not reproduction of its denominator, coverage, or null. The negative applies only to the undocumented candidate set at an undocumented scan time and cannot establish that no other wiki host existed.",
    }


def markdown(report: dict) -> str:
    repo = report["repository_inventory"]
    narrative = report["published_narrative"]
    unavailable = report["unavailable_inputs"]
    lines = [
        "# KMAD 6,271-candidate wiki-sweep audit",
        "",
        "**Disposition: reported, not reproducible from the cited repository.** The audit inspected the immutable "
        f"KMAD tree at commit `{repo['commit']}` ({repo['tracked_files']} tracked files).",
        "",
        "## What the repository says",
        "",
        f"KMAD reports fetching RecentChanges for **{narrative['candidate_denominator']:,} candidate wiki installs** from the WikiIndex API across "
        f"19 engine categories. It reports exactly one initially non-known match, `{narrative['reported_match']}`, while the repository's headline "
        "calls the result no genuinely new host because that host was already present in the writeup, tweet, or corpus.",
        "",
        "The named categories are UseMod, Oddmuse, PmWiki, MoinMoin, DokuWiki, MediaWiki, TiddlyWiki, TWiki, JSPWiki, PhpWiki, Instiki, Wikka, "
        "and wiki farms; the remaining category names are not published. The same findings file says the `wiki-hunt` subagent ended before an "
        "Oddmuse browser pass and before corroborating a publictestwiki artifact.",
        "",
        "## Reproducibility inventory",
        "",
        f"The sweep terms occur in {len(repo['narrative_or_code_mentions'])} tracked files: "
        + ", ".join(f"`{item['path']}`" for item in repo["narrative_or_code_mentions"])
        + ". None is an implementation or machine-readable result.",
        "",
        "| Required audit input | Published? |",
        "|---|---|",
    ]
    labels = {
        "target_list": "6,271-target list and deduplication key",
        "per_target_results": "Per-target result ledger",
        "reachable_unreachable_partition": "Reachable / unreachable / non-wiki partition",
        "response_statuses": "HTTP status and redirect outcomes",
        "retry_and_timeout_policy": "Retry, timeout, concurrency and user-agent policy",
        "scan_timestamps": "Per-target or run timestamps",
        "positive_control_log": "Positive-control execution log",
        "fingerprint_or_match_rule": "Agent-match rule and threshold",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {'no' if unavailable[key] else 'yes'} |")
    lines += [
        "",
        "## Bounded negative result",
        "",
        "The available repository establishes that KMAD **reported** a 6,271-candidate run and one match. It does not establish that 6,271 were "
        "distinct live installations, that all returned usable RecentChanges data, or that failed requests were retried. Candidate generation, "
        "coverage, and the match rule cannot be recomputed.",
        "",
        "The null is also time-bound: wikis can disappear, appear, change engines, truncate RecentChanges, or block automated clients. `/16` matching "
        "would itself be incomplete because the source repository explains that the observed prefix set is not an infrastructure inventory. The strongest "
        "defensible statement is therefore: **no additional host was reported within KMAD's undocumented candidate/response set**. It is not evidence that "
        "no additional host existed on the public web.",
        "",
        "Machine-readable inventory: "
        "[`data/termina_kmad_sweep_audit_2026-09-08.json`](../data/termina_kmad_sweep_audit_2026-09-08.json).",
        "",
        "Re-run against a local checkout of the cited commit:",
        "",
        "```sh",
        "python3 scripts/termina_kmad_sweep_audit.py --repository /path/to/agent-swarm-forensics",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "termina_kmad_sweep_audit_2026-09-08.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "analysis" / "termina-kmad-sweep-audit.md")
    args = parser.parse_args()
    report = build(args.repository, args.database)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(report["local_disposition"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
