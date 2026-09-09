#!/usr/bin/env python3
"""Fixture tests for host_inventory_sweep.py. No network."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_inventory_sweep as his  # noqa: E402


def rev(body="", name="", when="2026-06-18T12:00:00Z"):
    return {"body": body, "name": name, "page_id": "", "change_summary": "",
            "time": when}


class TestClassify(unittest.TestCase):
    def test_known_classes(self):
        cases = {
            "bvryr-16-146-184-55.run.pinggy-free.link": "tunnel",
            "localtunnel.me": "tunnel",
            "www-sec-gov.translate.goog": "translate_proxy",
            "memgator.cs.odu.edu": "archive_proxy",
            "tsl.preservica.com": "archive_proxy",
            "platform.lemino.ai": "reader_proxy",
            "r.jina.ai": "reader_proxy",
            "allorigins.hexlet.app": "cors_proxy",
            "x.blob.core.windows.net": "cloud_blob",
            "vanderbi.lt": "shortener",
            "wikiservice.at": "wiki_host",
            "www.sec.gov": "endpoint",
        }
        for host, expected in cases.items():
            self.assertEqual(his.classify(host), expected, host)

    def test_unknown_host_is_an_endpoint(self):
        self.assertEqual(his.classify("data.example.gov"), "endpoint")


class TestScan(unittest.TestCase):
    def test_percent_encoded_hostname_is_flagged(self):
        out = his.scan([rev(body="see https://%61llorigins.hexlet.app/raw?url=x")])
        self.assertIn("%61llorigins.hexlet.app", out["encoded_hosts"])

    def test_plain_hostname_is_not_flagged_as_encoded(self):
        out = his.scan([rev(body="https://allorigins.hexlet.app/raw?url=x")])
        self.assertEqual(dict(out["encoded_hosts"]), {})

    def test_first_seen_is_the_earliest_revision(self):
        out = his.scan([
            rev(body="https://a.example.org/x", when="2026-06-18T12:00:00Z"),
            rev(body="https://a.example.org/y", when="2026-05-24T09:00:00Z"),
        ])
        self.assertEqual(out["first_seen"]["a.example.org"], "2026-05-24T09:00:00Z")

    def test_counts_occurrences_not_revisions(self):
        out = his.scan([rev(body="https://a.example.org/1 https://a.example.org/2")])
        self.assertEqual(out["hosts"]["a.example.org"], 2)

    def test_revision_without_a_url_is_still_counted(self):
        out = his.scan([rev(body="Describe the new page here.")])
        self.assertEqual(out["revisions"], 1)
        self.assertEqual(len(out["hosts"]), 0)


class TestCredentialHandling(unittest.TestCase):
    SECRET = "b45cdbc8-9568-4008-9091-8da10b5b91f1"

    def setUp(self):
        self.out = his.scan([
            rev(body=f"https://tsl.preservica.com/Render/x?token={self.SECRET}&scope=ua"),
            rev(body="https://tsl.preservica.com/Render/y?token=34dc3f06-58dd-4d7a-b&scope=ua"),
        ])

    def test_counts_occurrences_and_distinct_values(self):
        self.assertEqual(self.out["credential_params"]["token"]["occurrences"], 2)
        self.assertEqual(self.out["credential_params"]["token"]["distinct_values"], 2)

    def test_value_never_appears_in_the_output(self):
        blob = json.dumps(his.build(self.out))
        self.assertNotIn(self.SECRET, blob)
        self.assertNotIn(self.SECRET[:12], blob)


class TestCatalogueDiff(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "surfaces.md").write_text(
            "we already know about Allorigins.Hexlet.App here\n", encoding="utf-8")
        self.scanned = his.scan([
            rev(body="https://allorigins.hexlet.app/raw?url=x"),
            rev(body="https://platform.lemino.ai/api/url2md/y"),
        ])

    def test_catalogued_host_is_matched_case_insensitively(self):
        data = his.build(self.scanned, his.catalogue_text(self.root))
        rows = {r["host"]: r for r in data["hosts"]}
        self.assertTrue(rows["allorigins.hexlet.app"]["catalogued"])
        self.assertFalse(rows["platform.lemino.ai"]["catalogued"])
        self.assertEqual(data["uncatalogued_count"], 1)

    def test_generated_inventories_are_excluded_from_the_catalogue(self):
        """A machine inventory is not a catalogue: including one lets a sweep's
        own output -- or a sibling sweep's -- silently cancel its findings."""
        for stem in his.GENERATED_INVENTORIES:
            (self.root / f"{stem}_2026-09-09.json").write_text(
                json.dumps({"hosts": [{"host": "platform.lemino.ai"}]}),
                encoding="utf-8")
        naive = his.catalogue_text(self.root)
        guarded = his.catalogue_text(self.root, skip_names=his.GENERATED_INVENTORIES)
        self.assertIn("platform.lemino.ai", naive)
        self.assertNotIn("platform.lemino.ai", guarded)

    def test_generated_inventories_covers_the_sibling_sweep(self):
        self.assertIn("shortener_export_crosscheck", his.GENERATED_INVENTORIES)
        self.assertIn("host_inventory_sweep", his.GENERATED_INVENTORIES)

    def test_no_catalogue_leaves_the_flag_unset(self):
        data = his.build(self.scanned)
        self.assertIsNone(data["hosts"][0]["catalogued"])
        self.assertNotIn("uncatalogued_count", data)


if __name__ == "__main__":
    unittest.main()
