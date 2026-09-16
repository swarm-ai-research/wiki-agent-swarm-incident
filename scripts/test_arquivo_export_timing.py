import json
import tempfile
import unittest
from pathlib import Path

import arquivo_export_timing as timing


def capture(host, ts, url):
    return {"timestamp": ts, "url": url, "status": "200", "filename": f"save-{ts}.warc.gz"}


def revision(rev_id, utc, body, label="AgentX"):
    return {"rev_id": rev_id, "time": utc, "body": body, "label": label}


class TimingTests(unittest.TestCase):
    def build(self, rows, revisions):
        directory = Path(tempfile.mkdtemp())
        path = directory / "revisions.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in revisions))
        raw = {host: {"window_rows": [row for h, row in rows if h == host]}
               for host in {h for h, _ in rows}}
        return timing.build(raw, path)

    def test_orders_and_separates_generic_matches(self):
        rows = [
            ("portal.max.gov", capture("portal.max.gov", "20260526073119",
                                       "https://portal.max.gov/portal/document/SF133/a/1.pdf")),
            ("httpbin.org", capture("httpbin.org", "20260526073119", "https://httpbin.org/get")),
            ("api.microlink.io", capture("api.microlink.io", "20260617000000",
                                         "https://api.microlink.io/?url=https://datausa.io/api/x")),
        ]
        revisions = [
            revision("dse~A@1", "2026-05-26T10:57:38Z", "see https://portal.max.gov/portal/document/SF133/a/1.pdf"),
            revision("dse~B@1", "2026-05-26T09:00:00Z", "testing https://httpbin.org/get"),
            revision("dse~C@1", "2026-06-16T00:00:00Z", "cube at https://datausa.io/api/x"),
        ]
        report = self.build(rows, revisions)
        summary = report["summary"]
        self.assertEqual(summary["task_specific_matches"], 2)
        self.assertEqual(summary["generic_matches"], 1)
        self.assertEqual(summary["capture_first"], 1)   # max.gov
        self.assertEqual(summary["wiki_first"], 1)      # datausa via the proxy capture
        max_gov = next(m for m in report["matches"] if m["capture"]["host"] == "portal.max.gov")
        self.assertEqual(max_gov["wiki_minus_capture_seconds"], 12379)  # 07:31:19 -> 10:57:38
        self.assertEqual(max_gov["first_wiki_mention"]["rev_id"], "dse~A@1")

    def test_normalization_and_generic_rules(self):
        self.assertEqual(timing.normalize("HTTPS://WWW.Example.com/A/"), "example.com/a")
        self.assertTrue(timing.is_generic("example.com/anything"))
        self.assertTrue(timing.is_generic("lcdl.library.cofc.edu"))        # bare host
        self.assertTrue(timing.is_generic("is.gd/robots.txt"))
        self.assertFalse(timing.is_generic("lcdl.library.cofc.edu/lcdl/catalog/lcdl:129229"))
        self.assertEqual(timing.embedded("https://api.microlink.io/?url=https://x.org/a"), "x.org/a")


if __name__ == "__main__":
    unittest.main()
