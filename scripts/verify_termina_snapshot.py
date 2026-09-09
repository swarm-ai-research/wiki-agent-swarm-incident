#!/usr/bin/env python3
"""Verify the pinned swarm.termina.digital incident-database snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


BASE = Path(__file__).resolve().parents[1] / "data" / "termina"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(base: Path) -> dict:
    manifest_path = base / "manifest.json"
    database_path = base / "incidents.sqlite"
    schema_path = base / "schema.json"
    snapshot = json.loads((base / "snapshot.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    json.loads(schema_path.read_text(encoding="utf-8"))

    if snapshot["schema_version"] != manifest["schema_version"]:
        raise ValueError("snapshot and manifest schema versions differ")
    if snapshot["generated_at"] != manifest["generated_at"]:
        raise ValueError("snapshot and manifest generation times differ")
    for filename, metadata in snapshot["files"].items():
        path = base / filename
        actual_size = path.stat().st_size
        if actual_size != metadata["bytes"]:
            raise ValueError(
                f"{filename}: expected {metadata['bytes']} bytes, got {actual_size}"
            )
        actual_hash = sha256(path)
        if actual_hash != metadata["sha256"]:
            raise ValueError(
                f"{filename}: expected {metadata['sha256']}, got {actual_hash}"
            )

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")

        counts = {}
        for filename, metadata in manifest["files"].items():
            table = filename.removesuffix(".jsonl")
            quoted = table.replace('"', '""')
            actual = connection.execute(
                f'SELECT count(*) FROM "{quoted}"'
            ).fetchone()[0]
            expected = metadata["rows"]
            if actual != expected:
                raise ValueError(
                    f"{table}: manifest says {expected} rows, SQLite has {actual}"
                )
            counts[table] = actual
    finally:
        connection.close()

    checked_jsonl = {}
    for filename, metadata in manifest["files"].items():
        path = base / filename
        if not path.exists():
            continue
        actual = sha256(path)
        if actual != metadata["sha256"]:
            raise ValueError(
                f"{filename}: expected {metadata['sha256']}, got {actual}"
            )
        checked_jsonl[filename] = actual

    return {
        "schema_version": manifest["schema_version"],
        "generated_at": manifest["generated_at"],
        "sqlite_sha256": snapshot["files"]["incidents.sqlite"]["sha256"],
        "tables_checked": len(counts),
        "manifest_rows": sum(counts.values()),
        "jsonl_files_checked": sorted(checked_jsonl),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=BASE)
    args = parser.parse_args()
    print(json.dumps(verify(args.dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
