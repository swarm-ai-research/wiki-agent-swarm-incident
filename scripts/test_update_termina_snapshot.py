import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import update_termina_snapshot as updater


TABLES = updater.DELTA_TABLES


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_release(path, generation="new", version="9", extra_claim=False, corrupt=False):
    path.mkdir(parents=True)
    database = path / "incidents.sqlite"
    connection = sqlite3.connect(database)
    for table in TABLES:
        connection.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, value TEXT)')
        connection.execute(f'INSERT INTO "{table}" VALUES (?, ?)', (f"{table}-1", "old"))
    if extra_claim:
        connection.execute("INSERT INTO claim VALUES ('claim-2', 'new')")
        connection.execute("UPDATE venue SET value='changed' WHERE id='venue-1'")
    connection.commit()
    connection.close()
    files = {
        f"{table}.jsonl": {"rows": 2 if table == "claim" and extra_claim else 1, "sha256": "unused"}
        for table in TABLES
    }
    (path / "manifest.json").write_text(json.dumps({
        "schema_version": version, "generated_at": generation, "files": files
    }))
    (path / "schema.json").write_text(json.dumps({
        "version": version,
        "tables": {table: {"properties": {"id": {"type": "string"}, "value": {"type": "string"}}} for table in TABLES},
    }))
    if corrupt:
        database.write_bytes(b"not sqlite")


def install_current(source, destination):
    destination.mkdir()
    for name in updater.CORE_FILES:
        (destination / name).write_bytes((source / name).read_bytes())
    snapshot = {
        "source": str(source) + "/", "generated_at": "old", "schema_version": "9",
        "files": {name: {"bytes": (destination / name).stat().st_size, "sha256": digest(destination / name)} for name in updater.CORE_FILES},
    }
    (destination / "snapshot.json").write_text(json.dumps(snapshot))


class UpdateTerminaSnapshotTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        old, new, destination = root / "old", root / "new", root / "destination"
        make_release(old, generation="old")
        install_current(old, destination)
        return temporary, new, destination

    def test_unchanged_dry_run_does_not_modify_destination(self):
        temporary, source, destination = self.fixture()
        with temporary:
            make_release(source, generation="old")
            before = digest(destination / "incidents.sqlite")
            report = updater.update(str(source), destination, dry_run=True)
            self.assertFalse(report["promoted"])
            self.assertEqual(report["delta"]["entities"]["claim"]["added"], 0)
            self.assertEqual(digest(destination / "incidents.sqlite"), before)

    def test_changed_release_reports_and_promotes_semantic_delta(self):
        temporary, source, destination = self.fixture()
        with temporary:
            make_release(source, extra_claim=True)
            report = updater.update(str(source), destination)
            self.assertTrue(report["promoted"])
            self.assertEqual(report["delta"]["entities"]["claim"]["added"], 1)
            self.assertEqual(report["delta"]["entities"]["venue"]["changed"], 1)
            self.assertEqual(json.loads((destination / "snapshot.json").read_text())["generated_at"], "new")

    def test_corrupt_database_is_rejected_without_overwrite(self):
        temporary, source, destination = self.fixture()
        with temporary:
            make_release(source, corrupt=True)
            before = digest(destination / "incidents.sqlite")
            with self.assertRaisesRegex(ValueError, "invalid incidents.sqlite"):
                updater.update(str(source), destination)
            self.assertEqual(digest(destination / "incidents.sqlite"), before)

    def test_incompatible_schema_is_rejected_without_overwrite(self):
        temporary, source, destination = self.fixture()
        with temporary:
            make_release(source, version="10")
            before = digest(destination / "manifest.json")
            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                updater.update(str(source), destination)
            self.assertEqual(digest(destination / "manifest.json"), before)


if __name__ == "__main__":
    unittest.main()
