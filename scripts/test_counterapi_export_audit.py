import json
import tempfile
import unittest
from pathlib import Path

import counterapi_export_audit as audit


class CounterApiExportAuditTests(unittest.TestCase):
    def test_fixture_classifies_protocols_without_fetching(self):
        rows = [
            {"rev_id": "dse~A@1", "wiki": "dse", "time": "2026-06-16T00:00:00Z", "body": "https://api.counterapi.dev/v1/ns/ack/up", "hunks": [{"op": "insert", "b0": 0, "b1": 1}]},
            {"rev_id": "dse~A@2", "wiki": "dse", "time": "2026-06-17T00:00:00Z", "body": "https://api.counterapi.dev/v1/ns/answer/set?count=CODE", "hunks": [{"op": "insert", "b0": 0, "b1": 1}]},
            {"rev_id": "dse~A@3", "wiki": "dse", "time": "2026-06-18T00:00:00Z", "body": "recruit unrelated task: https://api.counterapi.dev/%76%31/ns/signal/up https://countapi.mileshilliard.com/hit/x/y", "hunks": [{"op": "insert", "b0": 0, "b1": 1}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "revisions.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            report = audit.build([path])
        self.assertEqual(report["summary"]["verbs"], {"hit": 1, "set": 1, "up": 2})
        self.assertEqual(report["subclaims"]["set_count_value_channel"]["matching_revisions"], 1)
        self.assertEqual(report["subclaims"]["unrelated_task_recruitment_to_counter"]["matching_revisions"], 1)
        self.assertEqual(report["subclaims"]["percent_encoded_v1_path"]["matching_revisions"], 1)

    def test_committed_report_reproduces_published_inventory(self):
        report = json.loads(audit.OUTPUT.read_text())
        self.assertEqual(report["summary"]["counter_url_occurrences"], 607)
        self.assertEqual(report["summary"]["matching_revisions"], 420)
        self.assertEqual(report["summary"]["verbs"], {"bare": 9, "hit": 44, "set": 53, "up": 501})


if __name__ == "__main__":
    unittest.main()
