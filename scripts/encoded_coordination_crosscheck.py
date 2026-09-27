#!/usr/bin/env python3
"""Cross-check the "Encoded Coordination on the Open Web" quote tables against
the collusion.wiki revision export.

Offline only. Reads the companion repository's evidence JSON
(ethanelasky/collusion-on-the-open-web, docs/counter-investigations/*.json) and
the hash-pinned collusion.wiki export that repository vendors. Never requests a
wiki, CounterAPI, CountAPI, or any recorded URL, and writes no revision text:
the output holds rev ids, timestamps, and match verdicts only.

For each quoted excerpt that cites a revision, it asks:
  present   the excerpt occurs in that revision's body (whitespace-normalised;
            "..." / "…" ellipses split the excerpt into parts that must all occur),
            or in its concatenated inserted hunks (flagged `spans_hunks`)
  new       at least one part occurs in text the revision inserted or replaced,
            rather than only in text inherited from the previous revision
  time      the cited UTC time equals the revision's export time (to the second)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "encoded_coordination_crosscheck_2026-09-27.json"
COMPANION_REPO = "https://github.com/ethanelasky/collusion-on-the-open-web"
COMPANION_COMMIT = "3fd0238"
EVIDENCE_FILES = [
    "direct-answer-evidence.json",
    "state-codes.json",
    "country-flags.json",
    "heartbeat.json",
    "location-ack-evidence.json",
    "wage-soc.json",
]
# Pinned in the companion repo's ai_collusion/wiki.py (2026-09-03 export).
EXPORT_SHA256 = {
    "revisions.jsonl": "60df4a515178230aa952d9f64f6215aea4bd95ab2f05e31e484cf9b887e3f793",
    "manifest.json": "b6d53e16b5d9a6a0a98d4577238835ee7a574d7d10a8f1312330b4e626c6ba2b",
}
QUOTE_KEYS = ("quote", "text", "exact_excerpt", "excerpt", "added_text", "changed_text")
REF_KEYS = ("rev_id", "revision", "url", "source_url", "source", "wiki_url")
TIME_KEYS = ("published_utc", "publication_utc", "time_utc", "time", "published")
REV_URL = re.compile(r"/page/([a-z0-9]+)~([^#/?\s.]+)(?:\.html)?#rev-(\d+)", re.I)
REV_ID = re.compile(r"^([a-z0-9]+)~([^@\s]+)@(\d+)$", re.I)
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def inserted_text(row: dict) -> str:
    lines = (row.get("body") or "").splitlines(True)
    return "".join(
        "".join(lines[h.get("b0", 0):h.get("b1", 0)])
        for h in row.get("hunks") or []
        if h.get("op") in {"insert", "replace"}
    )


def rev_key(ref: str) -> str | None:
    ref = ref.strip()
    m = REV_ID.match(ref)
    if m:
        return f"{m.group(1)}~{m.group(2)}@{m.group(3)}"
    m = REV_URL.search(ref)
    if m:
        return f"{m.group(1)}~{m.group(2)}@{m.group(3)}"
    return None


def parse_time(value: str) -> datetime | None:
    value = value.strip().replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value[:19] if fmt.endswith("%S") else value[:16], fmt)
        except ValueError:
            continue
    return None


def walk(node, source: str, out: list[dict]) -> None:
    if isinstance(node, dict):
        quote = next((node[k] for k in QUOTE_KEYS if isinstance(node.get(k), str)), None)
        ref = next((node[k] for k in REF_KEYS
                    if isinstance(node.get(k), str) and rev_key(node[k])), None)
        if quote and ref:
            when = next((node[k] for k in TIME_KEYS if isinstance(node.get(k), str)), None)
            out.append({"source": source, "rev": rev_key(ref), "quote": quote, "time": when})
        for value in node.values():
            walk(value, source, out)
    elif isinstance(node, list):
        for value in node:
            walk(value, source, out)


def mutate(quote: str) -> str | None:
    """Negative control: shift the first digit (or swap the first letter's case)."""
    for i, ch in enumerate(quote):
        if ch.isdigit():
            return quote[:i] + str((int(ch) + 1) % 10) + quote[i + 1:]
    for i, ch in enumerate(quote):
        if ch.isalpha():
            return quote[:i] + ch.swapcase() + quote[i + 1:]
    return None


def check(claim: dict, revs: dict) -> dict:
    row = revs.get(claim["rev"])
    result = {"source": claim["source"], "rev": claim["rev"], "cited_time": claim["time"]}
    if row is None:
        return {**result, "found_rev": False}
    parts = [norm(p) for p in ELLIPSIS.split(claim["quote"]) if norm(p)]
    # Drop wrapping quote marks the tables add around excerpts.
    parts = [p.strip('"') for p in parts if p.strip('"')]
    body, new = norm(row.get("body") or ""), norm(inserted_text(row))
    cited = parse_time(claim["time"]) if claim["time"] else None
    actual = parse_time(row.get("time") or "")
    return {
        **result,
        "found_rev": True,
        "export_time": row.get("time"),
        "label": row.get("label"),
        "parts": len(parts),
        # Companion `added_text` fields concatenate every inserted hunk, so an
        # excerpt spanning two hunks is contiguous only in the inserted text.
        "present": all(p in body or p in new for p in parts),
        "spans_hunks": not all(p in body for p in parts) and all(p in new for p in parts),
        "new": any(p in new for p in parts),
        "time_match": None if cited is None or actual is None
        else abs((cited - actual).total_seconds()) < 1
        or (len(claim["time"].strip()) <= 16 and cited == actual.replace(second=0)),
    }


def build(companion: Path, export: Path) -> dict:
    hashes = {name: digest(export / name) for name in EXPORT_SHA256}
    revs = {}
    with (export / "revisions.jsonl").open() as fh:
        for line in fh:
            row = json.loads(line)
            revs[row["rev_id"]] = row
    claims: list[dict] = []
    for name in EVIDENCE_FILES:
        walk(json.loads((companion / "docs" / "counter-investigations" / name).read_text()),
             name, claims)
    # Same (rev, quote) can be cited from several files; check each once.
    unique = {(c["rev"], norm(c["quote"])): c for c in claims}
    results = [check(c, revs) for c in unique.values()]
    # A checker that cannot fail proves nothing: every mutated excerpt must miss.
    controls = [check({**c, "quote": mutate(c["quote"])}, revs)
                for c in unique.values() if mutate(c["quote"])]
    tally = Counter()
    for r in results:
        tally["checked"] += 1
        if not r["found_rev"]:
            tally["rev_missing"] += 1
            continue
        tally["present"] += r["present"]
        tally["new_in_revision"] += r["new"]
        if r["time_match"] is not None:
            tally["time_cited"] += 1
            tally["time_match"] += r["time_match"]
    by_source = Counter(r["source"] for r in results)
    return {
        "generated": "2026-09-27",
        "companion_repo": COMPANION_REPO,
        "companion_commit": COMPANION_COMMIT,
        "export_sha256": hashes,
        "export_hash_matches_pin": hashes == EXPORT_SHA256,
        "export_revisions": len(revs),
        "claims_extracted": len(claims),
        "claims_unique": len(unique),
        "by_source_file": dict(sorted(by_source.items())),
        "tally": dict(tally),
        "negative_control": {"mutated": len(controls),
                             "still_present": sum(bool(r.get("present")) for r in controls)},
        "failures": [r for r in results
                     if not r["found_rev"] or not r["present"] or r["time_match"] is False],
        "spans_hunks": [r["rev"] for r in results if r.get("spans_hunks")],
        "inherited_only": [r["rev"] for r in results if r.get("present") and not r["new"]],
        "results": sorted(results, key=lambda r: (r["source"], r["rev"])),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("companion", type=Path, help="checkout of collusion-on-the-open-web")
    ap.add_argument("--export", type=Path,
                    help="collusion.wiki export dir (default: <companion>/data/collusion-wiki)")
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args()
    report = build(args.companion, args.export or args.companion / "data" / "collusion-wiki")
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("export_hash_matches_pin", "claims_unique", "by_source_file", "tally",
                       "negative_control")},
                     indent=2))
    print(f"failures: {len(report['failures'])}  inherited-only: {len(report['inherited_only'])}")


if __name__ == "__main__":
    main()
