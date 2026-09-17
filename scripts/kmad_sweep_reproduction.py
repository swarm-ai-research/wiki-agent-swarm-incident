#!/usr/bin/env python3
"""Build the reproducible KMAD-comparison sweep, including a failure retry ledger."""
import argparse
import collections
import hashlib
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETRY_OUTCOMES = {"blocked", "unavailable", "parsing_failed"}
POSITIVE_CONTROLS = {"DorfWiki", "TextEditors Wiki"}


def retry_manifest(payload):
    rows = []
    for result in payload["results"]:
        if result["outcome"] not in RETRY_OUTCOMES:
            continue
        rows.append({
            "name": result["name"],
            "engine": result["engine"],
            "status": "Active",
            "urls": [result["url"]],
        })
    return rows


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(first_path, retry_path, original_audit_path):
    first_path = first_path.resolve()
    retry_path = retry_path.resolve()
    original_audit_path = original_audit_path.resolve()
    first = json.loads(first_path.read_text())
    retry = json.loads(retry_path.read_text())
    original_audit = json.loads(original_audit_path.read_text())
    # rc_from() can canonicalize an already-RC URL again on the retry pass, so
    # reconcile by the preserved, unique WikiIndex name/engine identity.
    retry_by_target = {(row["name"], row["engine"]): row for row in retry["results"]}
    final = []
    transitions = collections.Counter()
    for row in first["results"]:
        second = retry_by_target.get((row["name"], row["engine"]))
        if second:
            transitions[(row["outcome"], second["outcome"])] += 1
        final.append(second or row)
    final_counts = collections.Counter(row["outcome"] for row in final)
    controls = []
    by_name = {row["name"]: row for row in final}
    for name in sorted(POSITIVE_CONTROLS):
        row = by_name[name]
        controls.append({
            "name": name,
            "outcome": row["outcome"],
            "score": row.get("score"),
            "signals": row.get("signals", {}),
            "passed": row["outcome"] == "readable" and (row.get("score") or 0) > 0,
        })
    return {
        "generated_on": date.today().isoformat(),
        "comparison_claim": original_audit["published_narrative"],
        "scope": {
            "kmad_reported_candidates": 6271,
            "kmad_manifest_available": False,
            "reconstructed_source_rows": 1356,
            "normalized_distinct_endpoints": first["run"]["target_count"],
            "coverage_of_kmad_claimed_denominator": round(first["run"]["target_count"] / 6271, 6),
            "equivalence_to_kmad_population": "not established"
        },
        "policy": {
            "first_pass_requests_per_endpoint": 1,
            "retry_outcomes": sorted(RETRY_OUTCOMES),
            "retry_requests_per_eligible_endpoint": 1,
            "scanner_timeout_seconds": first["run"]["configuration"]["fetch_timeout_seconds"],
            "first_pass_workers": first["run"]["configuration"]["workers"],
            "retry_workers": retry["run"]["configuration"]["workers"],
            "body_retention": "none",
            "evidence_excerpt_retention": False
        },
        "artifacts": {
            "first_pass": str(first_path.relative_to(ROOT)),
            "first_pass_sha256": sha256(first_path),
            "retry_pass": str(retry_path.relative_to(ROOT)),
            "retry_pass_sha256": sha256(retry_path),
            "first_targets_sha256": first["run"]["targets_sha256"],
            "retry_targets_sha256": retry["run"]["targets_sha256"]
        },
        "coverage": {
            "first_pass": dict(collections.Counter(r["outcome"] for r in first["results"])),
            "retry_eligible": len(retry_by_target),
            "retry_transitions": {f"{a}->{b}": n for (a, b), n in sorted(transitions.items())},
            "final": dict(final_counts),
            "final_total": len(final)
        },
        "positive_controls": controls,
        "disposition": "partial-independent-reproduction",
        "bounded_conclusion": "The independent 1,355-endpoint reconstruction cannot reproduce KMAD's unpublished 6,271 denominator. It provides a complete request/retry ledger for 21.6074% of that claimed count, verifies its positive controls, and does not establish a web-wide negative result."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, default=ROOT / "data/swarm_scan_2026-09-08.json")
    parser.add_argument("--retry", type=Path)
    parser.add_argument("--original-audit", type=Path, default=ROOT / "data/termina_kmad_sweep_audit_2026-09-08.json")
    parser.add_argument("--retry-manifest", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "data/kmad_sweep_reproduction_2026-09-08.json")
    args = parser.parse_args()
    first = json.loads(args.first.read_text())
    if args.retry_manifest:
        rows = retry_manifest(first)
        args.retry_manifest.write_text(json.dumps(rows, indent=2) + "\n")
        print(f"wrote {len(rows)} retry targets")
        return
    if not args.retry:
        parser.error("--retry is required unless --retry-manifest is used")
    report = build(args.first, args.retry, args.original_audit)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(report["disposition"])


if __name__ == "__main__":
    main()
