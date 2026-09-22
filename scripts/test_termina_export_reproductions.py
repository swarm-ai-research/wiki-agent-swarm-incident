import json
import unittest

import termina_export_reproductions as rep


class PrefixTests(unittest.TestCase):
    def test_ipv4_16(self):
        self.assertEqual(rep.ipv4_16("ip:20.165.4.x"), "20.165")
        self.assertEqual(rep.ipv4_16("20.165"), "20.165")
        self.assertIsNone(rep.ipv4_16("ip:*"))
        self.assertIsNone(rep.ipv4_16("ip:999.1.x.x"))


class JoinTests(unittest.TestCase):
    def test_window_baseline_and_unusable_rows(self):
        links = [
            ("2026-04-30T23:59", "ip:20.1.1.x"),  # before the window
            ("2026-05-01T00:00", "ip:20.1.2.x"),
            ("2026-06-18T12:00", "ip:20.1.3.x"),
            ("2026-06-18T12:01", "ip:34.92.0.x"),
            ("2026-06-18T12:02", "ip:*"),
            ("2026-09-04T00:00", "ip:20.1.4.x"),  # after
        ]
        report = rep.rmn_join(links, {"20.1"})
        window = report["window"]
        self.assertEqual(window["links"], 4)
        self.assertEqual(window["links_without_ipv4"], 1)
        self.assertEqual(window["ipv4_prefixes"], 2)
        self.assertEqual(window["prefixes"], 3)
        self.assertEqual(window["shared_prefixes"], 1)
        self.assertEqual(window["links_on_shared_prefixes"], 2)
        self.assertEqual(report["before"]["links"], 1)
        self.assertEqual(report["after"]["links"], 1)


class ScanTests(unittest.TestCase):
    def test_moderators_skipped_and_vocabulary_counted(self):
        rows = [
            {"wiki": "dse", "page_id": "dse/A", "label": "Agent1", "body": "R3 due task 11:17:20; please post"},
            {"wiki": "dse", "page_id": "dse/A", "label": "[Admin1]", "body": "deadline"},
            {"wiki": "probier", "page_id": "probier/B", "label": "Agent2", "body": "https://example.org/data.json"},
        ]
        scan = rep.coordination_scan(rows)["per_wiki"]
        self.assertEqual(scan["dse"], {"revisions": 1, "revisions_with_vocabulary": 1, "pages_with_vocabulary": 1})
        self.assertEqual(scan["probier"]["revisions_with_vocabulary"], 0)


class PinnedResultTests(unittest.TestCase):
    """The committed JSON must still carry the numbers the cross-check cites."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(rep.OUT.read_text(encoding="utf-8"))

    def test_rmn_claim_numbers(self):
        window = self.report["rmn_re_shares_wiki_networks"]["window"]
        self.assertEqual(
            (window["links"], window["prefixes"], window["shared_prefixes"], window["links_on_shared_prefixes"]),
            (540, 123, 106, 494),
        )
        self.assertEqual(self.report["rmn_re_shares_wiki_networks"]["before"]["shared_prefixes"], 0)

    def test_retrieval_venues_silent_against_dse_control(self):
        scan = self.report["retrieval_venues_never_talk"]["per_wiki"]
        self.assertEqual(scan["probier"]["revisions_with_vocabulary"], 0)
        self.assertEqual(scan["fractal"]["revisions_with_vocabulary"], 0)
        self.assertGreater(scan["dse"]["revisions_with_vocabulary"], 1000)


if __name__ == "__main__":
    unittest.main()
