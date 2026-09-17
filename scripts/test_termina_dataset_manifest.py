import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "termina"
MANIFEST = DATA / "curated-datasets-manifest-2026-09-08.json"
PROVENANCE = DATA / "curated-datasets-manifest-provenance.json"
EXPECTED_SHA256 = "a04225aef2e1c900dcf3b8940da2b135edd7c058b3d005c38ce6e5b94c57d52f"


class TerminaDatasetManifestTests(unittest.TestCase):
    def test_recovered_manifest_bytes_are_pinned(self):
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), EXPECTED_SHA256)
        provenance = json.loads(PROVENANCE.read_text())
        self.assertEqual(provenance["artifact_sha256"], EXPECTED_SHA256)
        self.assertEqual(provenance["mirror"]["downloaded_sha256"], "a2d637451c97fc6c9c5b504326a9a594ca14a9d616db8f1fa42c8b485e65388f")
        self.assertFalse(provenance["scope"]["bundle_files_held_here"])

    def test_manifest_rows_are_well_formed_and_unique(self):
        rows = json.loads(MANIFEST.read_text())["datasets"]
        self.assertEqual(len(rows), 9)
        self.assertEqual(len({row["file"] for row in rows}), 9)
        for row in rows:
            self.assertGreater(row["bytes"], 0)
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(row["file"].endswith(".tar.gz"))

    def test_archive_readmes_do_not_link_to_dead_manifest(self):
        dead_href = re.compile(r"\]\([^)]*/pub/datasets/manifest\.json[^)]*\)")
        for readme in (ROOT / "README.md", DATA / "README.md"):
            self.assertIsNone(dead_href.search(readme.read_text()), readme)


if __name__ == "__main__":
    unittest.main()
