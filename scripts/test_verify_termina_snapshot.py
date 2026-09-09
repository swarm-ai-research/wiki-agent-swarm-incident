"""Tests for scripts/verify_termina_snapshot.py.

This script is the integrity gate for the pinned schema-v9 Termina snapshot:
every downstream analysis trusts it to notice if the local copy stops matching
what was pinned. Each way the snapshot can drift gets a case, because a gate
that silently passes is worse than no gate.
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import verify_termina_snapshot as vts  # noqa: E402


GENERATED_AT = "2026-09-08T14:10:58.029664541Z"
SCHEMA_VERSION = 9


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def build_snapshot(base, tables=None, jsonl=True):
    """Write a miniature but structurally faithful snapshot directory."""
    base = Path(base)
    tables = tables if tables is not None else {"venue": 2, "claim": 3}

    database = base / "incidents.sqlite"
    connection = sqlite3.connect(database)
    try:
        for table, rows in tables.items():
            connection.execute(f'CREATE TABLE "{table}" (id TEXT)')
            connection.executemany(
                f'INSERT INTO "{table}" VALUES (?)',
                [(f"{table}-{i}",) for i in range(rows)],
            )
        connection.commit()
    finally:
        connection.close()

    manifest_files = {}
    for table, rows in tables.items():
        payload = "".join(
            json.dumps({"id": f"{table}-{i}"}) + "\n" for i in range(rows)
        ).encode("utf-8")
        entry = {"rows": rows, "sha256": sha256_bytes(payload)}
        if jsonl:
            (base / f"{table}.jsonl").write_bytes(payload)
        manifest_files[f"{table}.jsonl"] = entry

    (base / "schema.json").write_text(json.dumps({"version": SCHEMA_VERSION}), encoding="utf-8")
    (base / "manifest.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "generated_at": GENERATED_AT,
        "files": manifest_files,
    }), encoding="utf-8")

    db_bytes = database.read_bytes()
    (base / "snapshot.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "generated_at": GENERATED_AT,
        "files": {
            "incidents.sqlite": {"bytes": len(db_bytes), "sha256": sha256_bytes(db_bytes)},
        },
    }), encoding="utf-8")
    return base


def edit_json(path, mutate):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    mutate(data)
    Path(path).write_text(json.dumps(data), encoding="utf-8")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = build_snapshot(self._tmp.name)

    def test_a_matching_snapshot_reports_what_it_checked(self):
        result = vts.verify(self.base)
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(result["generated_at"], GENERATED_AT)
        self.assertEqual(result["tables_checked"], 2)
        self.assertEqual(result["manifest_rows"], 5)
        self.assertEqual(result["jsonl_files_checked"], ["claim.jsonl", "venue.jsonl"])
        self.assertEqual(
            result["sqlite_sha256"], vts.sha256(self.base / "incidents.sqlite")
        )

    def test_a_changed_database_fails_the_hash_check(self):
        with sqlite3.connect(self.base / "incidents.sqlite") as connection:
            connection.execute("INSERT INTO venue VALUES ('smuggled')")
        with self.assertRaises(ValueError) as caught:
            vts.verify(self.base)
        self.assertIn("incidents.sqlite", str(caught.exception))

    def test_a_size_mismatch_is_caught_before_the_hash(self):
        edit_json(self.base / "snapshot.json",
                  lambda d: d["files"]["incidents.sqlite"].update(bytes=1))
        with self.assertRaisesRegex(ValueError, "expected 1 bytes"):
            vts.verify(self.base)

    def test_a_hash_mismatch_is_reported_with_the_expected_digest(self):
        edit_json(self.base / "snapshot.json",
                  lambda d: d["files"]["incidents.sqlite"].update(sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "0{64}"):
            vts.verify(self.base)

    def test_snapshot_and_manifest_must_agree_on_schema_version(self):
        edit_json(self.base / "manifest.json", lambda d: d.update(schema_version=8))
        with self.assertRaisesRegex(ValueError, "schema versions differ"):
            vts.verify(self.base)

    def test_snapshot_and_manifest_must_agree_on_generation_time(self):
        edit_json(self.base / "manifest.json",
                  lambda d: d.update(generated_at="2026-09-07T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "generation times differ"):
            vts.verify(self.base)

    def test_a_row_count_that_disagrees_with_the_manifest_fails(self):
        edit_json(self.base / "manifest.json",
                  lambda d: d["files"]["venue.jsonl"].update(rows=99))
        with self.assertRaisesRegex(ValueError, "venue: manifest says 99 rows, SQLite has 2"):
            vts.verify(self.base)

    def test_a_table_named_in_the_manifest_but_missing_from_sqlite_fails(self):
        edit_json(self.base / "manifest.json",
                  lambda d: d["files"].update({"ghost.jsonl": {"rows": 1, "sha256": "0" * 64}}))
        with self.assertRaises(sqlite3.OperationalError):
            vts.verify(self.base)

    def test_a_tampered_jsonl_sidecar_fails_even_though_sqlite_is_intact(self):
        (self.base / "venue.jsonl").write_bytes(b'{"id": "tampered"}\n')
        with self.assertRaisesRegex(ValueError, "venue.jsonl"):
            vts.verify(self.base)

    def test_jsonl_sidecars_are_optional(self):
        # The pinned directory carries only some of the manifest's jsonl files.
        (self.base / "venue.jsonl").unlink()
        result = vts.verify(self.base)
        self.assertEqual(result["jsonl_files_checked"], ["claim.jsonl"])
        self.assertEqual(result["tables_checked"], 2)

    def test_a_corrupt_schema_file_is_rejected(self):
        (self.base / "schema.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            vts.verify(self.base)


class Sha256Tests(unittest.TestCase):
    def test_chunked_hashing_matches_hashlib_over_the_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.bin"
            payload = b"termina" * 400_000  # larger than the 1 MiB read chunk
            path.write_bytes(payload)
            self.assertEqual(vts.sha256(path), sha256_bytes(payload))


class PinnedSnapshotTests(unittest.TestCase):
    """Runs against the real pinned snapshot when it is present."""

    def test_the_committed_snapshot_verifies(self):
        if not (vts.BASE / "incidents.sqlite").exists():
            self.skipTest("pinned snapshot not present")
        result = vts.verify(vts.BASE)
        # Upstream pins the version as a string; verify() compares snapshot and
        # manifest with ==, so the two files must agree on type as well as value.
        self.assertEqual(str(result["schema_version"]), str(SCHEMA_VERSION))
        self.assertEqual(result["generated_at"], GENERATED_AT)
        self.assertGreater(result["manifest_rows"], 0)


if __name__ == "__main__":
    unittest.main()
