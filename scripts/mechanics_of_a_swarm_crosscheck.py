#!/usr/bin/env python3
"""Cross-check PhilflowIO/agent-swarm-forensics against what this archive holds.

The repository ships derived tables for the paper "The Mechanics of a Swarm"
(Lütje 2026) but not the collusion.wiki export they were computed from, so only
part of it is checkable here. Three things are:

1. its daily save/delete series against the nine-wiki series this archive holds
   from JoshuaDavid's agent-logs (`data/daily_counts.json`),
2. the arithmetic of its population estimator, which inverts a uniform-marker
   occupancy model over a 365-day marker space,
3. the uniform-marker assumption itself, calibrated against the 298 audited run
   identities this archive holds from the fast-follow trajectories repository
   (`data/run_identity_map.json`) — a set reconstructed without that model.

Everything that needs the export or its page text stays unverified here and is
listed as such in the report.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY_COUNTS = ROOT / "data" / "daily_counts.json"
RUN_IDENTITY_MAP = ROOT / "data" / "run_identity_map.json"
DEFAULT_OUT = ROOT / "data" / "mechanics_of_a_swarm_crosscheck_2026-09-14.json"
DEFAULT_REPOSITORY = Path("/home/user/philflowio/agent-swarm-forensics")

# Marker space of the paper's population estimator: one (month, day) pair per
# episode, 365 calendar-valid pairs (53_mathematik.py: N = 365).
MARKER_SPACE = 365
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_DAYS = dict(zip(MONTHS, [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]))
MARKER_RE = re.compile("(" + "|".join(MONTHS) + r")(\d{1,2})")
# chi-square, 11 degrees of freedom, upper 5% point; kept as a constant so the
# script stays stdlib-only.
CHI2_DF11_P05 = 19.675

# Wikis both series carry. `dorfwiki` is compared on the total only: this
# archive's series folds it into an `other` column with four wikis outside the
# export cut (apchem, texteditors, milkwiki, ludism).
SHARED_WIKIS = {"dse": "saves_dse", "probier": "saves_probier", "fractal": "saves_fractal"}
DORFWIKI_TOTAL_IN_ARCHIVE = 6  # README.md, "The numbers (from the public export)"
CONTROL_DAYS = ["2026-06-18", "2026-07-14"]


def repository_provenance(repository: Path) -> dict:
    """Pin what was read: commit, tag, and the licence files the repo ships."""
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(repository), *args],
                                 check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        return out.stdout.strip() or None

    return {
        "path": str(repository),
        "commit": git("rev-parse", "HEAD"),
        "tag": git("describe", "--tags", "--exact-match"),
        "commit_date": git("log", "-1", "--format=%cI"),
        "licence_files": sorted(p.name for p in repository.glob("LICENSE*")),
        **export_redistribution(repository),
    }


EXPORT_FILES = ["pages.jsonl", "revisions.jsonl", "events.jsonl", "labels.jsonl",
                "manifest.json"]


def export_redistribution(repository: Path) -> dict:
    """Does the repository re-host the do-not-share export, or only pin it?"""
    data_dir = repository / "analyse" / "data"
    sums = data_dir / "SHA256SUMS"
    pinned = []
    if sums.exists():
        pinned = [line.split()[-1] for line in sums.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    return {
        "export_files_present": sorted(name for name in EXPORT_FILES
                                       if (data_dir / name).exists()),
        "export_files_pinned_by_hash": sorted(pinned),
    }


def compare_daily(repo_rows: list[dict], archive_rows: list[dict]) -> dict:
    """Day-by-day comparison of the two reconstructions of the same farm."""
    archive = {row["d"]: row for row in archive_rows}
    days, mismatches, missing = [], [], []
    totals = {"repository": Counter(), "archive": Counter()}
    for row in repo_rows:
        day = row["date"]
        for wiki, column in SHARED_WIKIS.items():
            totals["repository"][wiki] += int(row[column])
        totals["repository"]["del"] += int(row["deletes"])
        totals["repository"]["dorfwiki"] += int(row["saves_dorfwiki"])
        held = archive.get(day)
        if held is None:
            missing.append(day)
            continue
        days.append(day)
        for wiki, column in SHARED_WIKIS.items():
            if int(row[column]) != int(held[wiki]):
                mismatches.append({"day": day, "field": wiki,
                                   "repository": int(row[column]), "archive": int(held[wiki])})
        if int(row["deletes"]) != int(held["del"]):
            mismatches.append({"day": day, "field": "del",
                               "repository": int(row["deletes"]), "archive": int(held["del"])})
    for row in archive_rows:
        for wiki in list(SHARED_WIKIS) + ["del", "wiki4d", "other"]:
            totals["archive"][wiki] += int(row[wiki])
    return {
        "days_compared": len(days),
        "days_in_repository_only": missing,
        "mismatches": mismatches,
        "controls_present": {day: day in days for day in CONTROL_DAYS},
        "totals": {
            "repository": dict(totals["repository"]),
            "archive_shared_wikis": {k: v for k, v in totals["archive"].items()
                                     if k in set(SHARED_WIKIS) | {"del"}},
            "archive_outside_export_cut": {k: totals["archive"][k] for k in ("wiki4d", "other")},
        },
        "dorfwiki_total_matches_archive_readme":
            totals["repository"]["dorfwiki"] == DORFWIKI_TOTAL_IN_ARCHIVE,
    }


def invert_occupancy(distinct_marks: int, marker_space: int = MARKER_SPACE) -> float:
    """Episodes implied by `distinct_marks` distinct markers under uniform draws.

    Inverts D = M (1 - (1 - 1/M)^N) for N.
    """
    if not 0 < distinct_marks < marker_space:
        raise ValueError("distinct marks must lie strictly inside the marker space")
    return math.log(1 - distinct_marks / marker_space) / math.log(1 - 1 / marker_space)


def expected_distinct(draws: int, marker_space: int = MARKER_SPACE) -> float:
    return marker_space * (1 - (1 - 1 / marker_space) ** draws)


def check_estimator(repository: Path) -> dict:
    """Recompute each published population estimate from its own observed D."""
    path = repository / "analyse" / "artefakte" / "paper_flotte_schaetzer_versoehnt.csv"
    rows = []
    for row in csv.DictReader(path.open(encoding="utf-8")):
        observed = int(row["beobachtet_D"])
        published = int(row["n_hat"])
        if observed <= 0:  # the assumption-free names:cohorts ratio row
            rows.append({"method": row["verfahren"], "observed_distinct_marks": None,
                         "published": published, "recomputed": None,
                         "note": "not an occupancy estimate"})
            continue
        recomputed = invert_occupancy(observed)
        rows.append({
            "method": row["verfahren"],
            "observed_distinct_marks": observed,
            "published": published,
            "recomputed": round(recomputed, 1),
            "relative_difference": round((recomputed - published) / published, 4),
            "ci": [int(row["ci_lo"]), int(row["ci_hi"])],
        })
    reproduced = [r for r in rows if r["recomputed"] is not None]
    return {
        "marker_space_days": MARKER_SPACE,
        "rows": rows,
        "max_relative_difference": max(abs(r["relative_difference"]) for r in reproduced),
    }


def marker_calibration(run_identity_map: dict) -> dict:
    """Calibrate the uniform-marker model on run identities built without it.

    The fast-follow reconstruction assembles runs from signoffs, content and
    exclusion rules, not from name markers. If each run is one episode and the
    marker is a uniform calendar draw, the distinct-marker count over those runs
    should track the occupancy curve, and the month histogram should follow the
    length of the months.
    """
    runs = run_identity_map["runs"]
    runs = runs.items() if isinstance(runs, dict) else runs
    supported, marks, undated = 0, [], []
    for _, meta in runs:
        if not meta.get("supported"):
            continue
        supported += 1
        found = MARKER_RE.search(meta["name"])
        if found and 1 <= int(found.group(2)) <= MONTH_DAYS[found.group(1)]:
            marks.append((found.group(1), int(found.group(2))))
        else:
            undated.append(meta["name"])
    draws, distinct = len(marks), len(set(marks))
    months = Counter(month for month, _ in marks)
    chi_square = sum((months.get(month, 0) - draws * MONTH_DAYS[month] / MARKER_SPACE) ** 2
                     / (draws * MONTH_DAYS[month] / MARKER_SPACE) for month in MONTHS)
    implied = invert_occupancy(distinct)
    return {
        "source": run_identity_map.get("source"),
        "supported_runs": supported,
        "runs_with_calendar_valid_marker": draws,
        "runs_without_marker": len(undated),
        "distinct_markers": distinct,
        "expected_distinct_under_uniform": round(expected_distinct(draws), 1),
        "implied_episodes_from_distinct": round(implied, 1),
        "audited_runs_for_comparison": draws,
        "implied_minus_audited_relative": round((implied - draws) / draws, 4),
        "month_histogram": {month: months.get(month, 0) for month in MONTHS},
        "month_chi_square": round(chi_square, 2),
        "month_chi_square_df": len(MONTHS) - 1,
        "month_chi_square_upper_5pct": CHI2_DF11_P05,
        "uniform_by_month_length_rejected_at_5pct": chi_square > CHI2_DF11_P05,
    }


def page_anchors(repository: Path) -> list[dict]:
    """Page-level anchors this archive already dates independently."""
    path = repository / "analyse" / "artefakte" / "hub_pages.csv"
    wanted = {"dse~DataUSAStateSequenceCollab2027": {
        "archive_claim": "first peer-directed write 2026-06-16T09:27Z, DataUSAStateSequenceCollab2027",
        "archive_ref": "data/termina_claim_crosscheck_2026-09-08.json "
                       "(claim dse-talk-arrives-with-the-clock, status verified)",
        "expected_prefix": "2026-06-16T09:27",
    }}
    anchors = []
    largest = repository / "analyse" / "artefakte" / "largest_pages.csv"
    for source in (path, largest):
        for row in csv.DictReader(source.open(encoding="utf-8")):
            key = row["page_key"]
            if key in wanted and not any(a["page"] == key for a in anchors):
                expect = wanted[key]
                anchors.append({
                    "page": key,
                    "repository_first_write": row["first_write"],
                    "archive_claim": expect["archive_claim"],
                    "archive_ref": expect["archive_ref"],
                    "agrees": row["first_write"].startswith(expect["expected_prefix"]),
                })
    return anchors


NOT_CHECKABLE_HERE = [
    {"claim": "876 episodes [774-995] behind 3,103 names",
     "why": "needs the export's name-level markers; only the arithmetic and the "
            "uniform-marker assumption are checkable here"},
    {"claim": "median internal/wall clock factor 0.435 in normal work (71 pairs, 53 names)",
     "why": "needs the agent-written clock statements in page text"},
    {"claim": "median 3.4 h upper-bound lead of the first report over a later cohort's arrival",
     "why": "needs per-item self-reported arrival times from page text"},
    {"claim": "no robust positive association between coordination behaviour and documented "
              "progress (510 of 907 reconstructed cohorts)",
     "why": "needs the classifier outputs over page text; outcome is censored and "
            "format-dependent by the paper's own account"},
    {"claim": "no correctness feedback documented (377 revisions, 205 names)",
     "why": "absence-of-evidence over page text; the export carries no harness messages"},
    {"claim": "egress almost entirely through allowlisted fetch services "
              "(4,682 revisions, 942 names); one POST path (29 revisions, 20 names)",
     "why": "needs the URL layer of the export; this archive's own surface inventory "
            "is built from a different extraction"},
    {"claim": "39,456 save attempts against 14,591 archived revisions",
     "why": "needs the export's event table"},
]


def build_report(repository: Path) -> dict:
    daily = json.loads(DAILY_COUNTS.read_text(encoding="utf-8"))
    repo_daily = list(csv.DictReader(
        (repository / "analyse" / "artefakte" / "wiki_daily.csv").open(encoding="utf-8")))
    return {
        "schema_version": 1,
        "generated": "2026-09-14",
        "source": {
            "repository": "https://github.com/PhilflowIO/agent-swarm-forensics",
            "paper": "The Mechanics of a Swarm: A Reproducible External Reconstruction of an "
                     "Unintended Agent-Coordination Episode on a Third-Party Wiki",
            "author": "Philipp Lütje (Philflow)",
            "concept_doi": "10.5281/zenodo.22689980",
            "licences": {"code": "MIT", "derived data": "CC BY 4.0"},
            **repository_provenance(repository),
        },
        "archive_inputs": {
            "daily_counts": daily.get("source"),
            "run_identity_map": str(RUN_IDENTITY_MAP.relative_to(ROOT)),
        },
        "daily_series": compare_daily(repo_daily, daily["rows"]),
        "population_estimator": check_estimator(repository),
        "marker_calibration": marker_calibration(
            json.loads(RUN_IDENTITY_MAP.read_text(encoding="utf-8"))),
        "page_anchors": page_anchors(repository),
        "not_checkable_here": NOT_CHECKABLE_HERE,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY,
                        help="clone of PhilflowIO/agent-swarm-forensics")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    report = build_report(args.repository)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    daily = report["daily_series"]
    print(f"days compared: {daily['days_compared']}, mismatches: {len(daily['mismatches'])}")
    print(f"estimator max relative difference: "
          f"{report['population_estimator']['max_relative_difference']}")
    cal = report["marker_calibration"]
    print(f"marker calibration: {cal['distinct_markers']} distinct over "
          f"{cal['runs_with_calendar_valid_marker']} audited runs, "
          f"expected {cal['expected_distinct_under_uniform']}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
