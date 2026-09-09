#!/usr/bin/env python3
"""Fixture tests for host_inventory_sweep.py. No network."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_inventory_sweep as his  # noqa: E402


def rev(body="", name="", when="2026-06-18T12:00:00Z", page_id=None, label=None):
    return {"body": body, "name": name, "page_id": page_id or f"dse/{name}",
            "change_summary": "", "time": when, "label": label}


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


class TestFamilies(unittest.TestCase):
    """Two hosts named together by ordinary task pages are one family."""

    def _scan(self, revisions):
        return his.scan(revisions)

    def test_repeated_co_occurrence_forms_a_family(self):
        scanned = self._scan([
            rev(body="https://a.example.org/1 https://b.example.org/1", name="RugbyRefsOne"),
            rev(body="https://a.example.org/2 https://b.example.org/2", name="RugbyRefsTwo"),
        ])
        fams = his.families(scanned, {"a.example.org", "b.example.org"})
        self.assertEqual(len(fams), 1)
        self.assertEqual(sorted(fams[0]["hosts"]), ["a.example.org", "b.example.org"])
        self.assertIn("Rugby", fams[0]["label"])

    def test_a_single_shared_page_is_not_a_family(self):
        """One proxy-menu page listing two hosts once is co-location, not a task."""
        scanned = self._scan([
            rev(body="https://a.example.org/1 https://b.example.org/1", name="ProxyMenu"),
        ])
        self.assertEqual(his.families(scanned, {"a.example.org", "b.example.org"}), [])

    def test_infrastructure_pages_do_not_join_hosts(self):
        """The farm's own pages list everything; they must not fuse families."""
        rows = [rev(body="https://a.example.org/x https://b.example.org/x",
                    name=page) for page in ("RecentChanges", "StartSeite", "SandBox")]
        scanned = self._scan(rows)
        self.assertEqual(his.families(scanned, {"a.example.org", "b.example.org"}), [])
        self.assertEqual(sum(scanned["infrastructure_revisions"].values()), 3)

    def test_family_spans_first_and_last_seen(self):
        scanned = self._scan([
            rev(body="https://a.example.org/1 https://b.example.org/1",
                name="TaskOne", when="2026-05-28T10:00:00Z"),
            rev(body="https://a.example.org/2 https://b.example.org/2",
                name="TaskTwo", when="2026-06-06T10:00:00Z"),
        ])
        fam = his.families(scanned, {"a.example.org", "b.example.org"})[0]
        self.assertEqual(fam["first_seen"], "2026-05-28T10:00:00Z")
        self.assertEqual(fam["last_seen"], "2026-06-06T10:00:00Z")


class TestBaselinePinning(unittest.TestCase):
    """Writing a host up makes it catalogued, which would dissolve its family."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.path = self.root / "prior_run.json"
        self.path.write_text(json.dumps({"hosts": [
            {"host": "a.example.org", "catalogued": False},
            {"host": "b.example.org", "catalogued": False},
            {"host": "known.example.org", "catalogued": True},
        ]}), encoding="utf-8")

    def test_baseline_reads_only_the_uncatalogued_hosts(self):
        self.assertEqual(his.baseline_hosts(self.path),
                         {"a.example.org", "b.example.org"})

    def test_pinned_family_survives_being_documented(self):
        revisions = [
            rev(body="https://a.example.org/1 https://b.example.org/1", name="TaskOne"),
            rev(body="https://a.example.org/2 https://b.example.org/2", name="TaskTwo"),
        ]
        scanned = his.scan(revisions)
        # A note now documents b.example.org, so it counts as catalogued.
        catalogue = "we wrote up b.example.org in an analysis note"
        unpinned = his.build(scanned, catalogue, with_families=True)
        pinned = his.build(scanned, catalogue, with_families=True,
                           family_hosts=his.baseline_hosts(self.path))
        self.assertEqual(unpinned["families"], [])
        self.assertEqual(len(pinned["families"]), 1)
        self.assertTrue(pinned["family_hosts_pinned"])
        self.assertEqual(pinned["family_host_count"], 2)


class TestPageAnchors(unittest.TestCase):
    """Recovers tasks that co-occurrence cannot see."""

    def test_host_whose_task_mates_are_catalogued_still_anchors(self):
        """The families blind spot: clustering only looks at uncatalogued hosts,
        so a host surrounded by catalogued ones falls out as a false singleton."""
        scanned = his.scan([
            rev(body="https://new.example.org/a https://known.example.org/a",
                name="TexasPdfTokenPath", label="AgentResearchMan"),
            rev(body="https://new.example.org/b https://known.example.org/b",
                name="TexasPdfTokenPath", label="AgentResearchRefresh"),
        ])
        fams = his.families(scanned, {"new.example.org"})
        self.assertEqual(fams, [], "a lone host cannot form a family")
        anchors = his.page_anchors(scanned, {"new.example.org"}, clustered=set())
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["anchor_page"], "dse/TexasPdfTokenPath")
        self.assertEqual(anchors[0]["page_revisions"], 2)

    def test_co_hosts_include_catalogued_hosts(self):
        """Catalogued neighbours are the context the host-only view discards."""
        scanned = his.scan([rev(body="https://new.example.org/a "
                                     "https://known.example.org/a", name="Task")])
        anchors = his.page_anchors(scanned, {"new.example.org"}, clustered=set())
        self.assertIn("known.example.org", anchors[0]["co_hosts"])
        self.assertNotIn("new.example.org", anchors[0]["co_hosts"])

    def test_labels_are_carried_but_bodies_are_not(self):
        scanned = his.scan([rev(body="https://new.example.org/?token=SECRETVALUE1",
                                name="Task", label="AgentX")])
        anchors = his.page_anchors(scanned, {"new.example.org"}, clustered=set())
        self.assertEqual(anchors[0]["labels"], ["AgentX"])
        self.assertNotIn("SECRETVALUE1", json.dumps(anchors))

    def test_infrastructure_page_is_never_an_anchor(self):
        scanned = his.scan([
            rev(body="https://new.example.org/a", name="StartSeite"),
            rev(body="https://new.example.org/b", name="RealTask"),
        ])
        anchors = his.page_anchors(scanned, {"new.example.org"}, clustered=set())
        self.assertEqual(anchors[0]["anchor_page"], "dse/RealTask")

    def test_host_only_on_infrastructure_pages_yields_no_anchor(self):
        scanned = his.scan([rev(body="https://new.example.org/a", name="StartSeite")])
        self.assertEqual(his.page_anchors(scanned, {"new.example.org"}, set()), [])

    def test_clustered_hosts_are_skipped(self):
        scanned = his.scan([rev(body="https://new.example.org/a", name="Task")])
        self.assertEqual(
            his.page_anchors(scanned, {"new.example.org"}, {"new.example.org"}), [])

    def test_same_page_name_on_two_wikis_is_two_pages(self):
        """Regression: page_counts and page_index must share one key, or the
        anchor lookup silently misses and every host looks anchorless."""
        scanned = his.scan([
            rev(body="https://new.example.org/a", name="Refs", page_id="dse/Refs"),
            rev(body="https://new.example.org/b", name="Refs", page_id="probier/Refs"),
        ])
        self.assertEqual(scanned["page_index"]["dse/Refs"]["revisions"], 1)
        self.assertEqual(scanned["page_index"]["probier/Refs"]["revisions"], 1)
        anchors = his.page_anchors(scanned, {"new.example.org"}, set())
        self.assertEqual(len(anchors), 1)
        self.assertIn(anchors[0]["anchor_page"], ("dse/Refs", "probier/Refs"))
        self.assertEqual(anchors[0]["page_revisions"], 1)


if __name__ == "__main__":
    unittest.main()
