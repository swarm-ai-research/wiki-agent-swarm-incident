"""Derive a stable scanner input manifest from the September 6 legacy target set."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/swarm_scan_2026-09-06.json"
DEFAULT_OUT = ROOT / "data/wikiindex_population_2026-09-06.json"


def derive(rows):
    entries = []
    seen = set()
    for row in rows:
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        entries.append({
            "name": row["name"],
            "engine": row.get("engine", "unknown"),
            "status": "Active",
            "urls": [url],
        })
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE, type=Path)
    parser.add_argument("--out", default=DEFAULT_OUT, type=Path)
    args = parser.parse_args()
    rows = json.loads(args.source.read_text())
    entries = derive(rows)
    args.out.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"wrote {len(entries)} targets to {args.out}")


if __name__ == "__main__":
    main()
