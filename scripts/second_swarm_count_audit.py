"""Summarize published evidence tables without copying payload or card bodies."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess


def audit(second, fleet):
    def revision(root):
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    results = {"second_swarm_commit": revision(second), "fleet_commit": revision(fleet)}
    entries = [line.split(maxsplit=1) for line in
               (second / "MANIFEST.sha256").read_text().splitlines() if line.strip()]
    listed = {name.removeprefix("./") for _, name in entries}
    results["second_swarm_manifest"] = {
        "entries": len(entries),
        "mismatches": [name for expected, name in entries
                       if digest(second / name) != expected],
        "unlisted": sorted(str(p.relative_to(second)) for p in second.rglob("*")
                           if p.is_file() and ".git" not in p.parts
                           and str(p.relative_to(second)) not in listed
                           and p.name != "MANIFEST.sha256"),
    }
    results["tables"] = {}
    for name, identity in [("FARM_FULL_INVENTORY.csv", "dataset_id"),
                           ("ACCOUNTS.csv", "account"),
                           ("ATTACK_DATASETS.csv", "dataset_id"),
                           ("removed_attack_datasets.csv", "id")]:
        path = fleet / "analysis" / name
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        summary = {"sha256": digest(path), "rows": len(rows),
                   "unique_ids": len({row[identity] for row in rows})}
        account = "account" if "account" in rows[0] else "author"
        summary["accounts"] = dict(sorted(collections.Counter(
            row[account] for row in rows).items()))
        if name == "FARM_FULL_INVENTORY.csv":
            summary["snapshots"] = dict(sorted(collections.Counter(
                snapshot for row in rows
                for snapshot in row["snapshots_seen"].split(",")).items()))
            summary["true_flags"] = {key: sum(row[key] == "True" for row in rows)
                                     for key in ["reference_config", "inline_b64", "h5_probe"]}
        results["tables"][name] = summary
    path = second / "02_hf_fleet/hf_fleet_final_sweep_20260829.md"
    rows = [line.split("|")[1:-1] for line in path.read_text().splitlines()
            if line.startswith("| ")]
    rows = [row for row in rows if len(row) == 6 and
            row[0].strip().startswith(("newpc", "oldpc", "xuniji"))]
    counts = [int(match.group(1)) if (match := re.match(r"\s*(\d+)", row[2]))
              else 0 for row in rows]
    results["final_sweep_table"] = {
        "sha256": digest(path), "account_rows": len(rows),
        "profile_200": sum("200" in row[1] for row in rows),
        "profile_404": sum("404" in row[1] for row in rows),
        "numeric_repo_total": sum(counts),
        "numeric_repo_total_profile_200": sum(
            count for row, count in zip(rows, counts) if "200" in row[1]),
    }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("second_swarm", type=Path)
    parser.add_argument("fleet_corpus", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.second_swarm, args.fleet_corpus), indent=2))
