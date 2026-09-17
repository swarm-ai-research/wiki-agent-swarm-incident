import json
import tempfile
import unittest
from pathlib import Path

import shortener_reverse_monitor as monitor


class ShortenerReverseMonitorTests(unittest.TestCase):
    def setUp(self):
        self.index = {
            "codes": [{"short_code": "sho.rt/a"}, {"short_code": "sho.rt/b"}],
        }
        self.sweep = {
            "listing_results": [{
                "venue_id": "short",
                "url": "https://sho.rt/admin/index.php?api_key=secret&page=2",
                "outcome": "read",
                "http": 200,
                "bytes": 12,
                "sha256": "new-digest",
                "error": None,
                "body": "must never be persisted",
            }],
            "archive_results": [{
                "short_code": "sho.rt/b",
                "http": 200,
                "error": None,
                "captures": [{
                    "timestamp": "20260909000000",
                    "original": "https://sho.rt/b",
                    "statuscode": "302",
                    "mimetype": "text/html",
                    "digest": "CDX-DIGEST",
                    "redirect": "https://example.org/data?token=secret&year=2026",
                }],
            }],
        }

    def test_snapshot_is_sanitized_and_body_free(self):
        snapshot = monitor.sanitize_snapshot(self.index, self.sweep)
        encoded = json.dumps(snapshot)
        self.assertNotIn("must never be persisted", encoded)
        self.assertNotIn("secret", encoded)
        self.assertIn("%5BREDACTED%5D", encoded)
        self.assertIn("year=2026", encoded)

    def test_diff_emits_stable_events_for_all_change_types(self):
        current = monitor.sanitize_snapshot(self.index, self.sweep)
        current["archive_status"].append({
            "short_code": "sho.rt/c", "http": 0, "error_class": "TimeoutError"
        })
        previous = {
            "snapshot": {
                "short_codes": ["sho.rt/a"],
                "listing_observations": [{
                    "url": current["listing_observations"][0]["url"],
                    "sha256": "old-digest",
                }],
                "cdx_captures": [],
            }
        }
        first = monitor.diff_snapshots(previous, current)
        second = monitor.diff_snapshots(previous, current)
        self.assertEqual(first, second)
        self.assertEqual(
            {row["kind"] for row in first},
            {"new_short_code", "listing_digest_changed", "new_cdx_capture", "transport_failure"},
        )

    def test_repeated_snapshot_does_not_repeat_change_events(self):
        snapshot = monitor.sanitize_snapshot(self.index, self.sweep)
        snapshot["archive_status"].append({
            "short_code": "sho.rt/c", "http": 0, "error_class": "TimeoutError"
        })
        events = monitor.diff_snapshots({"snapshot": snapshot}, snapshot)
        self.assertEqual(events, [])

    def test_error_message_is_reduced_to_exception_class(self):
        self.sweep["listing_results"][0]["error"] = "URLError: token=do-not-store"
        snapshot = monitor.sanitize_snapshot(self.index, self.sweep)
        encoded = json.dumps(snapshot)
        self.assertIn("URLError", encoded)
        self.assertNotIn("do-not-store", encoded)

    def test_append_only_writer_refuses_overwrite(self):
        manifest = {"run_utc": "2026-09-09T18:00:00Z", "snapshot": {}, "events": []}
        with tempfile.TemporaryDirectory() as directory:
            path = monitor.write_append_only(Path(directory), manifest)
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError):
                monitor.write_append_only(Path(directory), manifest)

    def test_latest_manifest_is_lexically_latest_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "2026-09-08T120000Z.json").write_text("{}", encoding="utf-8")
            latest = root / "2026-09-09T120000Z.json"
            latest.write_text("{}", encoding="utf-8")
            self.assertEqual(monitor.latest_manifest(root), latest)


if __name__ == "__main__":
    unittest.main()
