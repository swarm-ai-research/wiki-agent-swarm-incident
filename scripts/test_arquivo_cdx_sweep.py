import unittest

import arquivo_cdx_sweep as sweep


def record(count, rows, sources=("wayback_surfaces",)):
    return {"sources": list(sources), "all_time_http": 200, "all_time_captures": count,
            "all_time_capped": count >= 5000, "first": "20170606133033" if count else None,
            "last": "20260601000000" if count else None, "window_http": 200,
            "window_captures": len(rows), "window_capped": False, "window_parts": [["a", "b"]], "window_rows": rows}


class ReduceTests(unittest.TestCase):
    def test_outcomes_keep_absence_and_failure_distinct(self):
        raw = {
            "empty.example": record(0, []),
            "old.example": record(6, []),
            "assets.example": record(3, [{"timestamp": "20260520000000", "url": "http://assets.example/a.gif", "status": "200"}]),
            "trace.example": record(2, [{"timestamp": "20260526101010", "url": "http://trace.example/wiki.cgi?ZZZAgentBridge", "status": "200"},
                                       {"timestamp": "20260801000000", "url": "http://trace.example/", "status": "302"}]),
            "failed.example": {"sources": ["surfaces_md"], "all_time_http": None, "all_time_error": "timed out"},
        }
        summary, results = sweep.reduce(raw, 5000)
        outcome = {r["host"]: r["outcome"] for r in results}
        self.assertEqual(outcome["empty.example"], "no_captures")
        self.assertEqual(outcome["old.example"], "no_captures_since_2026_05_01")
        self.assertEqual(outcome["assets.example"], "no_captures_since_2026_05_01")
        self.assertEqual(outcome["trace.example"], "window_captures_with_url_signature")
        self.assertEqual(outcome["failed.example"], "query_failed")
        trace = next(r for r in results if r["host"] == "trace.example")
        self.assertEqual(trace["since_2026_05_01"]["status_200_nonasset"], 1)
        self.assertEqual(trace["wayback_window_nonasset"], 1)
        self.assertFalse(trace["since_2026_05_01"]["capped"])
        self.assertEqual(summary["hosts"], 5)

    def test_capped_windows_are_split_until_complete(self):
        stamps = [f"2026061712{m:02d}00" for m in range(60)]

        def fake_cdx(params):
            rows = [{"timestamp": s, "url": "http://h/", "status": "200"} for s in stamps
                    if params["from"] <= s <= params["to"]]
            return 200, rows[: params["limit"]], None

        original, sweep.cdx = sweep.cdx, fake_cdx
        try:
            code, rows, error, parts = sweep.window_rows("h", 25, 0, "20260617000000", "20260618000000")
        finally:
            sweep.cdx = original
        self.assertEqual(sorted(r["timestamp"] for r in rows), stamps)
        self.assertGreater(len(parts), 2)
        self.assertIsNone(error)

    def test_embedded_targets_and_redaction(self):
        url = "https://api.microlink.io/?url=https://vizhub.healthdata.org/gbd-results/php/data.php?z=1"
        self.assertEqual(sweep.embedded_host(url), "vizhub.healthdata.org")
        self.assertEqual(sweep.embedded_host("https://markdown.new/is.gd/abc"), None)
        self.assertEqual(sweep.embedded_host("https://proxymule.com/__PROXY__/https/vizhub.healthdata.org/x"),
                         "vizhub.healthdata.org")
        clean = sweep.redact("https://x.io/?email=a.b@guerrillamail.com&apikey=abc123&q=1")
        self.assertNotIn("guerrillamail", clean)
        self.assertNotIn("abc123", clean)
        self.assertEqual(sweep.collection({"filename": "save-20260616-x.warc.gz"}), "save")

    def test_filenames_and_non_surfaces_are_not_hosts(self):
        self.assertIsNone(sweep.normalize("https://github.com/x/y"))
        self.assertEqual(sweep.normalize("https://www.pure.md/x"), "pure.md")
        self.assertEqual(sweep.normalize("paste.linuxiarz.pl"), "paste.linuxiarz.pl")


if __name__ == "__main__":
    unittest.main()
