"""Build a deterministic rescan manifest for top readable scanner leads."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def select(payload, limit=15, known_names=("DorfWiki",)):
    readable = [r for r in payload["results"] if r.get("outcome") == "readable"]
    readable.sort(key=lambda r: (-(r.get("score") or 0), r["name"]))
    selected = readable[:limit]
    return [{
        "name": row["name"],
        "engine": row["engine"],
        "status": "Active",
        "urls": [row["url"]],
        "prior_score": row["score"],
        "prior_known_incident_host": row["name"] in known_names,
    } for row in selected]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, default=ROOT / "data/swarm_scan_2026-09-08.json")
    parser.add_argument("--out", type=Path, default=ROOT / "data/swarm_scan_top15_manifest_2026-09-08.json")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()
    manifest = select(json.loads(args.scan.read_text()), args.limit)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(manifest)} leads to {args.out}")


if __name__ == "__main__":
    main()
