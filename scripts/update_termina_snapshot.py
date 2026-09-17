#!/usr/bin/env python3
"""Safely stage, validate, diff, and promote a Termina database snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "termina"
CORE_FILES = ("manifest.json", "schema.json", "incidents.sqlite")
DELTA_TABLES = ("incident", "campaign", "venue", "claim", "evidence", "record")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_source(source: str, name: str, target: Path) -> None:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https", "file"}:
        url = urllib.parse.urljoin(source.rstrip("/") + "/", name)
        with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)
        return
    shutil.copyfile(Path(source) / name, target)


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"invalid {path.name}: expected an object")
    return value


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def validate_stage(stage: Path, supported_schema: str, convenience: tuple[str, ...]) -> dict:
    manifest = _load_json(stage / "manifest.json")
    schema = _load_json(stage / "schema.json")
    version = str(manifest.get("schema_version", ""))
    if version != supported_schema:
        raise ValueError(f"unsupported schema version {version!r}; expected {supported_schema!r}")
    if str(schema.get("version", "")) != version:
        raise ValueError("schema and manifest versions differ")
    if not manifest.get("generated_at") or not isinstance(manifest.get("files"), dict):
        raise ValueError("manifest lacks generated_at or files")
    schema_tables = schema.get("tables")
    if not isinstance(schema_tables, dict):
        raise ValueError("schema lacks a tables object")
    manifest_tables = {
        filename.removesuffix(".jsonl")
        for filename in manifest["files"]
        if filename.endswith(".jsonl")
    }
    if manifest_tables != set(schema_tables):
        raise ValueError("schema and manifest table sets differ")

    database = stage / "incidents.sqlite"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        available = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        counts = {}
        for filename, metadata in manifest["files"].items():
            if not filename.endswith(".jsonl"):
                continue
            table = filename.removesuffix(".jsonl")
            if table not in available:
                raise ValueError(f"manifest table {table!r} is absent from SQLite")
            actual = connection.execute(
                f"SELECT count(*) FROM {_quoted(table)}"
            ).fetchone()[0]
            expected = metadata.get("rows")
            if actual != expected:
                raise ValueError(f"{table}: manifest says {expected} rows, SQLite has {actual}")
            counts[table] = actual
    except sqlite3.DatabaseError as error:
        raise ValueError(f"invalid incidents.sqlite: {error}") from error
    finally:
        connection.close()

    for filename in convenience:
        metadata = manifest["files"].get(filename)
        if metadata is None:
            raise ValueError(f"manifest does not describe convenience export {filename}")
        if sha256(stage / filename) != metadata.get("sha256"):
            raise ValueError(f"{filename}: hash differs from manifest")
    return {"manifest": manifest, "schema": schema, "table_counts": counts}


def _primary_key(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    columns = connection.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
    key = tuple(row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5])
    if not key:
        raise ValueError(f"{table}: no primary key available for semantic diff")
    return key


def _rows_by_key(connection: sqlite3.Connection, table: str) -> dict[tuple, tuple]:
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quoted(table)})")]
    key_columns = _primary_key(connection, table)
    indexes = [columns.index(column) for column in key_columns]
    rows = connection.execute(f"SELECT * FROM {_quoted(table)}")
    return {tuple(row[index] for index in indexes): tuple(row) for row in rows}


def semantic_delta(old_database: Path | None, new_database: Path, manifest: dict, old_manifest: dict | None) -> dict:
    old_counts = (old_manifest or {}).get("files", {})
    table_counts = {}
    for filename, metadata in sorted(manifest["files"].items()):
        if filename.endswith(".jsonl"):
            previous = old_counts.get(filename, {}).get("rows", 0)
            current = metadata["rows"]
            table_counts[filename.removesuffix(".jsonl")] = {
                "before": previous,
                "after": current,
                "delta": current - previous,
            }

    entities = {}
    new_connection = sqlite3.connect(f"file:{new_database}?mode=ro", uri=True)
    old_connection = None
    if old_database and old_database.exists():
        old_connection = sqlite3.connect(f"file:{old_database}?mode=ro", uri=True)
    try:
        for table in DELTA_TABLES:
            new_rows = _rows_by_key(new_connection, table)
            old_rows = _rows_by_key(old_connection, table) if old_connection else {}
            new_keys, old_keys = set(new_rows), set(old_rows)
            entities[table] = {
                "added": len(new_keys - old_keys),
                "removed": len(old_keys - new_keys),
                "changed": sum(new_rows[key] != old_rows[key] for key in new_keys & old_keys),
                "before": len(old_rows),
                "after": len(new_rows),
            }
    finally:
        new_connection.close()
        if old_connection:
            old_connection.close()
    return {"tables": table_counts, "entities": entities}


def _snapshot(source: str, stage: Path, manifest: dict) -> dict:
    return {
        "source": source.rstrip("/") + "/",
        "generated_at": manifest["generated_at"],
        "schema_version": str(manifest["schema_version"]),
        "files": {
            name: {"bytes": (stage / name).stat().st_size, "sha256": sha256(stage / name)}
            for name in CORE_FILES
        },
    }


def update(source: str, destination: Path, dry_run: bool = False, supported_schema: str | None = None) -> dict:
    destination = destination.resolve()
    current_snapshot = _load_json(destination / "snapshot.json") if (destination / "snapshot.json").exists() else {}
    supported = supported_schema or str(current_snapshot.get("schema_version", ""))
    if not supported:
        raise ValueError("no supported schema supplied and no current snapshot exists")
    convenience = tuple(
        path.name for path in sorted(destination.glob("*.jsonl"))
        if path.name not in CORE_FILES
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".termina-update-", dir=destination.parent) as temp:
        stage = Path(temp)
        for name in CORE_FILES + convenience:
            _copy_source(source, name, stage / name)
        validated = validate_stage(stage, supported, convenience)
        old_manifest = _load_json(destination / "manifest.json") if (destination / "manifest.json").exists() else None
        old_schema = _load_json(destination / "schema.json") if (destination / "schema.json").exists() else None
        if old_schema and old_schema.get("tables") != validated["schema"].get("tables"):
            raise ValueError("schema structure changed without a supported version change")
        delta = semantic_delta(
            destination / "incidents.sqlite" if destination.exists() else None,
            stage / "incidents.sqlite",
            validated["manifest"],
            old_manifest,
        )
        snapshot = _snapshot(source, stage, validated["manifest"])
        (stage / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        report = {
            "dry_run": dry_run,
            "promoted": not dry_run,
            "from_generated_at": current_snapshot.get("generated_at"),
            "to_generated_at": validated["manifest"]["generated_at"],
            "schema_version": supported,
            "files": snapshot["files"],
            "delta": delta,
        }
        if not dry_run:
            destination.mkdir(parents=True, exist_ok=True)
            for name in CORE_FILES + convenience + ("snapshot.json",):
                os.replace(stage / name, destination / name)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Directory or URL containing the published files")
    parser.add_argument("--dir", type=Path, default=BASE)
    parser.add_argument("--supported-schema")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional path for the JSON delta report")
    args = parser.parse_args()
    source = args.source
    if not source:
        source = _load_json(args.dir / "snapshot.json").get("source")
    if not source:
        parser.error("--source is required when snapshot.json has no source")
    report = update(source, args.dir, args.dry_run, args.supported_schema)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
