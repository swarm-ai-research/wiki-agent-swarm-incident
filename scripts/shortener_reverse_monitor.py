#!/usr/bin/env python3
"""Create append-only, sanitized observations of the shortener layer.

The monitor delegates all network access to ``shortener_reverse_sweep``.  It
never requests a short URL, follows a redirect, or persists a response body.
Each run is compared with an explicit prior manifest and written with exclusive
create semantics so monitoring history cannot be overwritten accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import shortener_reverse_index as reverse_index
import shortener_reverse_sweep as reverse_sweep


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRAPH = ROOT / "graph.html"
DEFAULT_LEDGER = ROOT / "data" / "shortener_code_resolution_ledger_2026-09-08.json"
DEFAULT_DB = ROOT / "data" / "termina" / "incidents.sqlite"
SCHEMA_VERSION = 1
ERROR_CLASS = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}")


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _error_class(value: object) -> str | None:
    """Retain an exception class, never a possibly sensitive error message."""
    if not value:
        return None
    match = ERROR_CLASS.match(str(value).strip())
    return match.group(0) if match else "TransportError"


def _capture_key(row: dict) -> tuple:
    return tuple(row.get(key) for key in (
        "short_code", "timestamp", "original", "statuscode", "mimetype", "digest", "redirect"
    ))


def sanitize_snapshot(index: dict, sweep: dict) -> dict:
    """Reduce an index/sweep pair to reviewable metadata only."""
    codes = sorted(row["short_code"] for row in index.get("codes", []))
    listings = []
    for row in sweep.get("listing_results", []):
        listings.append({
            "venue_id": row.get("venue_id"),
            "url": reverse_sweep.redact_url(row.get("url", "")),
            "outcome": row.get("outcome"),
            "http": row.get("http"),
            "bytes": row.get("bytes"),
            "sha256": row.get("sha256"),
            "error_class": _error_class(row.get("error")),
        })
    listings.sort(key=lambda row: (row["url"], row.get("venue_id") or ""))

    captures = []
    archive_status = []
    for result in sweep.get("archive_results", []):
        code = result["short_code"]
        archive_status.append({
            "short_code": code,
            "http": result.get("http"),
            "error_class": _error_class(result.get("error")),
        })
        for row in result.get("captures", []):
            captures.append({
                "short_code": code,
                "timestamp": row.get("timestamp"),
                "original": reverse_sweep.redact_url(row.get("original") or ""),
                "statuscode": row.get("statuscode"),
                "mimetype": row.get("mimetype"),
                "digest": row.get("digest"),
                "redirect": reverse_sweep.redact_url(row.get("redirect") or "") or None,
            })
    captures.sort(key=_capture_key)
    archive_status.sort(key=lambda row: row["short_code"])
    return {
        "short_codes": codes,
        "listing_observations": listings,
        "archive_status": archive_status,
        "cdx_captures": captures,
    }


def _event(kind: str, subject: str, details: dict) -> dict:
    content = {"kind": kind, "subject": subject, "details": details}
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"event_id": digest[:20], **content}


def diff_snapshots(previous: dict | None, current: dict) -> list[dict]:
    """Return stable, deterministic events between two sanitized snapshots."""
    old = (previous or {}).get("snapshot", {})
    events = []

    old_codes = set(old.get("short_codes", []))
    for code in sorted(set(current["short_codes"]) - old_codes):
        events.append(_event("new_short_code", code, {}))

    old_listings = {row["url"]: row for row in old.get("listing_observations", [])}
    for row in current["listing_observations"]:
        prior = old_listings.get(row["url"])
        if row.get("sha256") and (not prior or prior.get("sha256") != row["sha256"]):
            events.append(_event("listing_digest_changed", row["url"], {
                "previous_sha256": prior.get("sha256") if prior else None,
                "sha256": row["sha256"],
                "bytes": row.get("bytes"),
                "http": row.get("http"),
            }))

    old_captures = {_capture_key(row) for row in old.get("cdx_captures", [])}
    for row in current["cdx_captures"]:
        if _capture_key(row) not in old_captures:
            events.append(_event("new_cdx_capture", row["short_code"], row))

    failure_rows = [
        ("listing", row["url"], row)
        for row in current["listing_observations"]
        if row.get("error_class") or row.get("http") == 0
    ] + [
        ("cdx", row["short_code"], row)
        for row in current["archive_status"]
        if row.get("error_class") or row.get("http") == 0
    ]
    old_failure_keys = {
        ("listing", row["url"], row.get("http"), row.get("error_class"))
        for row in old.get("listing_observations", [])
        if row.get("error_class") or row.get("http") == 0
    } | {
        ("cdx", row["short_code"], row.get("http"), row.get("error_class"))
        for row in old.get("archive_status", [])
        if row.get("error_class") or row.get("http") == 0
    }
    for surface, subject, row in failure_rows:
        if (surface, subject, row.get("http"), row.get("error_class")) in old_failure_keys:
            continue
        events.append(_event("transport_failure", subject, {
            "surface": surface,
            "http": row.get("http"),
            "error_class": row.get("error_class") or "TransportError",
        }))

    return sorted(events, key=lambda row: (row["kind"], row["subject"], row["event_id"]))


def build_manifest(
    index: dict, sweep: dict, previous: dict | None, *, run_utc: str, inputs: dict
) -> dict:
    snapshot = sanitize_snapshot(index, sweep)
    events = diff_snapshots(previous, snapshot)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_utc": run_utc,
        "network_policy": (
            "Wayback CDX and registry-listed public index/statistics pages only; "
            "no bare short URLs, redirects, counters, /up endpoints, invitations, or lure destinations"
        ),
        "inputs": inputs,
        "previous_run_utc": (previous or {}).get("run_utc"),
        "counts": {
            "short_codes": len(snapshot["short_codes"]),
            "listing_observations": len(snapshot["listing_observations"]),
            "cdx_captures": len(snapshot["cdx_captures"]),
            "events": len(events),
        },
        "events": events,
        "snapshot": snapshot,
    }


def latest_manifest(directory: Path) -> Path | None:
    candidates = sorted(directory.glob("*.json")) if directory.exists() else []
    return candidates[-1] if candidates else None


def write_append_only(directory: Path, manifest: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromisoformat(manifest["run_utc"].replace("Z", "+00:00"))
    path = directory / timestamp.strftime("%Y-%m-%dT%H%M%SZ.json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--no-cdx", action="store_true")
    parser.add_argument("--no-listings", action="store_true")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    previous_path = args.previous or latest_manifest(args.out_dir)
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path else None
    index = reverse_index.build(args.graph, args.ledger)
    with tempfile.TemporaryDirectory(prefix="shortener-monitor-") as directory:
        index_path = Path(directory) / "index.json"
        index_path.write_text(json.dumps(index), encoding="utf-8")
        sweep = reverse_sweep.run(
            index_path,
            args.db,
            do_cdx=not args.no_cdx,
            do_listings=not args.no_listings,
            timeout=args.timeout,
            delay=args.delay,
            workers=max(1, args.workers),
        )
    run_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(index, sweep, previous, run_utc=run_utc, inputs={
        "graph": _display_path(args.graph),
        "archive_ledger": _display_path(args.ledger),
        "termina_db": _display_path(args.db),
    })
    path = write_append_only(args.out_dir, manifest)
    print(json.dumps({"output": str(path), **manifest["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
