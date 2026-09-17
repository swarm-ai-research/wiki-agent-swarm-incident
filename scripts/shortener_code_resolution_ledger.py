#!/usr/bin/env python3
"""Build a complete offline disposition ledger for archived shortener-code reads."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "wayback_proxy_captures_2026-09-08.json"
OUTPUT = ROOT / "data" / "shortener_code_resolution_ledger_2026-09-08.json"
CODE = re.compile(r"(?:https?://)?((?:tinyurl\.com|da\.gd|is\.gd|v\.gd)/[A-Za-z0-9_-]+)", re.I)


def extract_code(url: str) -> str | None:
    match = CODE.search(url)
    return match.group(1) if match else None


def canonical(value: str) -> str:
    host, alias = value.split("/", 1)
    return f"{host.casefold()}/{alias}"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build(source: Path = SOURCE) -> dict:
    payload = json.loads(source.read_text(encoding="utf-8"))
    observations: dict[str, dict] = {}
    for reader_host, group in payload["hosts"].items():
        for row in group.get("rows", []):
            if row.get("class") not in {"shortener-code", "toolkit-shape"}:
                continue
            code = extract_code(row.get("url", ""))
            if not code:
                continue
            key = canonical(code)
            record = observations.setdefault(
                key,
                {"code": code, "observations": [], "source_presence": "saved-capture-row"},
            )
            record["observations"].append(
                {"reader_host": reader_host, "timestamp": row.get("ts"), "status": row.get("status"), "url": row["url"]}
            )

    resolution = payload["shortener_code_resolution"]
    resolved = {canonical(code): (code, detail) for code, detail in resolution["resolved"].items()}
    derivative_keys = {canonical("da.gd/fbKv")}
    for key, (code, _detail) in resolved.items():
        if key not in observations and key not in derivative_keys:
            observations[key] = {"code": code, "observations": [], "source_presence": "resolution-record-only"}

    rows = []
    for key in sorted(observations):
        row = observations[key]
        detail = resolved.get(key, (None, None))[1]
        if detail and "stub" in detail:
            disposition = "archived-stub-target-unrecoverable"
        elif detail:
            disposition = "archived-target-recovered"
        else:
            disposition = "no-archive-capture-found-in-2026-09-08-pass"
        rows.append({**row, "disposition": disposition, "resolution": detail})

    discovered_hops = []
    for key in sorted(derivative_keys):
        code, detail = resolved[key]
        discovered_hops.append({"code": code, "disposition": "archived-target-recovered", "resolution": detail})

    counts = Counter(row["disposition"] for row in rows)
    return {
        "generated_on": date.today().isoformat(),
        "source": display_path(source),
        "network_policy": "offline-only; no live shortener or Archive request",
        "scope": {
            "unique_source_codes": len(rows),
            "saved_row_codes": sum(row["source_presence"] == "saved-capture-row" for row in rows),
            "resolution_only_source_codes": sum(row["source_presence"] == "resolution-record-only" for row in rows),
            "discovered_intermediate_hops": len(discovered_hops),
            "narrative_count_previously_reported": 26,
            "scope_discrepancy": "The prose count of 26 is not reproducible from the saved rows; this ledger uses the explicit union instead.",
        },
        "counts": dict(sorted(counts.items())),
        "codes": rows,
        "discovered_hops": discovered_hops,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = build(args.source)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"scope": report["scope"], "counts": report["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
