import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

import handoff_edges

ROOT = Path(__file__).resolve().parents[1]


def dossier(run, messages):
    return {"trajectory_id": run, "self_name": run, "signature": run, "owned_messages": messages}


def message(rev, utc, text, base=None, page="dse/Board"):
    return {"revision_id": rev, "page_id": page, "utc": utc, "diff_base": base,
            "spans": [{"text": text}]}


def revision(rev, time, body, page="dse/Board"):
    return {"rev_id": rev, "page_id": page, "wiki": "dse", "time": time,
            "time_grade": "write_date", "body": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest()}


class BuilderTests(unittest.TestCase):
    def build(self, dossiers, revisions, corrupt=False):
        directory = Path(tempfile.mkdtemp())
        explorer = directory / "trajectory-explorer/public/data/assembled-trajectories"
        explorer.mkdir(parents=True)
        rows = ["id,name,supported,task_id"]
        for item in dossiers:
            (explorer / f"{item['trajectory_id']}.json").write_text(json.dumps(item))
            rows.append(f"{item['trajectory_id']},{item['trajectory_id']},True,cvd-deaths")
        (directory / "TRAJECTORIES.csv").write_text("\n".join(rows) + "\n")
        raw = "".join(json.dumps(r) + "\n" for r in revisions).encode()
        digest = "0" * 64 if corrupt else hashlib.sha256(raw).hexdigest()
        with zipfile.ZipFile(directory / "full-wiki-logs.zip", "w") as archive:
            archive.writestr("revisions.jsonl", raw)
            archive.writestr("SHA256SUMS", f"{digest}  revisions.jsonl\n")
        return handoff_edges.build(str(directory))

    def fixture(self):
        dossiers = [
            dossier("A", [message("dse~Board@1", "2026-06-16T10:00:00Z", "R2 due 11:07:54 -- A")]),
            dossier("B", [message("dse~Board@2", "2026-06-16T10:05:00Z", "@A our R2 due 11:07:54", "dse~Board@1")]),
            dossier("C", [message("dse~Other@1", "2026-06-16T10:06:00Z", "also 11:07:54", None, "dse/Other")]),
        ]
        revisions = [
            revision("dse~Board@1", "2026-06-16T10:00:00Z", "R2 due 11:07:54 -- A"),
            revision("dse~Board@2", "2026-06-16T10:05:00Z", "R2 due 11:07:54 -- A\n@A our R2 due 11:07:54"),
            revision("dse~Other@1", "2026-06-16T10:06:00Z", "also 11:07:54", "dse/Other"),
        ]
        return dossiers, revisions

    def test_exposed_recurrence_is_an_edge_and_unexposed_is_only_counted(self):
        out = self.build(*self.fixture())
        self.assertEqual(out["summary"]["edges"], 1)
        self.assertEqual(out["summary"]["unexposed_recurrences"], 1)
        edge = out["edges"][0]
        self.assertEqual((edge["source"]["run"], edge["target"]["run"]), ("A", "B"))
        self.assertEqual(edge["exposure"]["event"], "dse~Board@1")
        self.assertEqual(edge["handoff_candidate_status"], "inferred")
        self.assertTrue(edge["target_names_source_run"])
        self.assertEqual(edge["lag_seconds"], 300)

    def test_output_carries_no_token_values_or_text(self):
        rendered = json.dumps(self.build(*self.fixture()))
        self.assertNotIn("11:07:54", rendered)
        self.assertNotIn("R2 due", rendered)

    def test_export_checksum_is_enforced(self):
        with self.assertRaises(SystemExit):
            self.build(*self.fixture(), corrupt=True)


class CommittedEdgesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data/handoff_edges_v1.json").read_text())

    def test_edges_point_at_events_in_time_order(self):
        events = {event["event_id"]: event for event in self.data["events"]}
        for edge in self.data["edges"]:
            source = events[edge["source"]["event"]]
            target = events[edge["target"]["event"]]
            self.assertIn(edge["exposure"]["event"], events)
            self.assertLess(source["observed_at"]["utc"], target["observed_at"]["utc"])
            self.assertNotEqual(edge["source"]["run"], edge["target"]["run"])
            self.assertEqual(edge["handoff_candidate_status"], "inferred")
        self.assertEqual(len(self.data["edges"]), self.data["summary"]["edges"])

    def test_events_follow_the_spec_contract(self):
        spec = json.loads((ROOT / "data/swarm_detection_spec_v1.json").read_text())
        for event in self.data["events"]:
            self.assertEqual(event["evidence_status"], "export")
            self.assertIn(event["evidence_status"], spec["evidence_statuses"])
            self.assertEqual(event["action"]["verb"], "write")

    def test_no_clock_values_or_bodies_are_published(self):
        rendered = (ROOT / "data/handoff_edges_v1.json").read_text()
        self.assertIsNone(re.search(r'"\d{1,2}:\d{2}:\d{2}"', rendered))
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"text"', rendered)


if __name__ == "__main__":
    unittest.main()
