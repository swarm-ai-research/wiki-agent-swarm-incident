import json
import os
import tempfile
import unittest
from pathlib import Path

import mythos5_transcript_audit as audit


def write(records) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for record in records:
        handle.write(json.dumps(record) + "\n")
    handle.close()
    return Path(handle.name)


def message(index, role="Assistant", type_="TextMessage", timestamp=None, content=""):
    return {
        "record": "message",
        "index": index,
        "role": role,
        "type": type_,
        "timestamp": timestamp,
        "content": content,
    }


class SyntheticTranscriptTests(unittest.TestCase):
    """Shape tests on a hand-built file; no release data is committed here."""

    def test_injected_turns_do_not_stretch_the_model_span(self):
        # The harness turns carry a late export stamp, as they do in the release.
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(0, role="System", timestamp="2026-07-18T21:00:00Z"),
                message(1, timestamp="2026-07-18T01:00:00Z"),
                message(2, timestamp="2026-07-18T03:00:00Z"),
            ]
        )
        timing = audit.audit(path)["timing"]
        self.assertEqual(timing["model_span_seconds"], 2 * 3600)
        self.assertEqual(timing["file_span_seconds"], 20 * 3600)
        path.unlink()

    def test_missing_indices_are_reported_as_a_contiguous_cut(self):
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(0, timestamp="2026-07-18T01:00:00Z"),
                message(4, timestamp="2026-07-18T02:00:00Z"),
            ]
        )
        redactions = audit.audit(path)["redactions"]
        self.assertEqual(redactions["missing_indices"], 3)
        self.assertEqual(redactions["missing_range"], [1, 3])
        path.unlink()

    def test_inline_redaction_kinds_are_counted(self):
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(
                    0,
                    timestamp="2026-07-18T01:00:00Z",
                    content="[redacted-key] x [redacted-key]",
                ),
                message(1, timestamp="2026-07-18T01:01:00Z", content="[redacted-ip-1]"),
            ]
        )
        redactions = audit.audit(path)["redactions"]
        self.assertEqual(redactions["inline_markers"], 3)
        self.assertEqual(redactions["inline_kinds"]["key"], 2)
        path.unlink()

    def test_one_counter_value_across_boundaries_is_one_compaction(self):
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(0, timestamp="2026-07-18T01:00:00Z"),
                message(
                    1,
                    role="Human",
                    timestamp="2026-07-18T21:00:00Z",
                    content="Summarization budget: 1/10 cycles used.",
                ),
                message(
                    2,
                    role="Human",
                    timestamp="2026-07-18T21:00:01Z",
                    content="Summarization budget: 1/10 cycles used (9 remaining).",
                ),
            ]
        )
        compaction = audit.audit(path)["compaction"]
        self.assertEqual(compaction["compactions_in_window"], 1)
        self.assertEqual([b["index"] for b in compaction["boundaries"]], [1, 2])
        path.unlink()

    def test_a_stamp_without_a_zone_is_read_as_utc(self):
        self.assertEqual(
            audit.parse_timestamp("2026-07-18T01:00:00"),
            audit.parse_timestamp("2026-07-18T01:00:00Z"),
        )

    def test_nearest_rank_percentile_is_deterministic(self):
        self.assertEqual(audit.percentile([4, 1, 3, 2], 50), 2)
        self.assertEqual(audit.percentile([4, 1, 3, 2], 95), 4)
        self.assertEqual(audit.percentile([], 95), 0.0)

    def test_sequence_counts_gaps_transitions_and_runs(self):
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(0, timestamp="2026-07-18T01:00:00Z"),
                {
                    **message(
                        1,
                        type_="ToolMessage",
                        timestamp="2026-07-18T01:01:00Z",
                    ),
                    "tool_name": "terminal",
                },
                {
                    **message(
                        2,
                        type_="ToolMessage",
                        timestamp="2026-07-18T01:02:01Z",
                    ),
                    "tool_name": "terminal",
                },
                {
                    **message(
                        3,
                        type_="ToolMessage",
                        timestamp="2026-07-18T01:02:02Z",
                    ),
                    "tool_name": "view_tool",
                },
            ]
        )
        sequence = audit.audit(path)["sequence"]
        self.assertEqual(sequence["gap_seconds"]["at_least_60"], 2)
        self.assertEqual(sequence["tool_transitions"]["terminal->terminal"], 1)
        self.assertEqual(sequence["tool_transitions"]["terminal->view_tool"], 1)
        self.assertEqual(sequence["longest_same_tool_run"]["calls"], 2)
        path.unlink()

    def test_marker_output_contains_counts_but_not_source_text(self):
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(
                    0,
                    timestamp="2026-07-18T01:00:00Z",
                    content="SIMULATION pypi fixture-secret-should-not-escape",
                ),
            ]
        )
        result = audit.audit(path)
        self.assertEqual(result["markers"]["records_with_marker"]["pypi"], 1)
        self.assertEqual(result["markers"]["record_cooccurrence"]["pypi+simulation"], 1)
        self.assertNotIn("fixture-secret-should-not-escape", json.dumps(result))
        path.unlink()


RELEASE = os.environ.get("MYTHOS5_TRANSCRIPT")


@unittest.skipUnless(
    RELEASE and Path(RELEASE).exists(),
    "set MYTHOS5_TRANSCRIPT to a local transcript.jsonl from "
    "anthropics/mythos-5-incident-transcript to check the published figures",
)
class ReleaseTests(unittest.TestCase):
    """Pin the numbers quoted in analysis/mythos5-transcript-audit.md.

    The release is not redistributed here, so these are opt-in.
    """

    @classmethod
    def setUpClass(cls):
        cls.result = audit.audit(Path(RELEASE))

    def test_message_and_index_counts(self):
        self.assertEqual(self.result["messages"], 2064)
        self.assertEqual(self.result["index_range"], [0, 2144])
        self.assertEqual(
            self.result["roles"], {"Assistant": 2061, "Human": 2, "System": 1}
        )
        self.assertEqual(
            self.result["types"], {"ToolMessage": 1361, "TextMessage": 703}
        )

    def test_head_redaction_matches_the_stated_cut(self):
        # The release notes say messages 1-81 inclusive were removed.
        self.assertEqual(self.result["redactions"]["missing_indices"], 81)
        self.assertEqual(self.result["redactions"]["missing_range"], [1, 81])

    def test_inline_redaction_total(self):
        self.assertEqual(self.result["redactions"]["inline_markers"], 2607)
        self.assertEqual(self.result["redactions"]["inline_kinds"]["service"], 1228)

    def test_file_span_is_about_double_the_model_span(self):
        timing = self.result["timing"]
        self.assertAlmostEqual(timing["model_span_seconds"], 37514.87, places=1)
        self.assertAlmostEqual(timing["file_span_seconds"], 73593.49, places=1)
        self.assertGreater(
            timing["file_span_seconds"], 1.9 * timing["model_span_seconds"]
        )

    def test_only_the_first_compaction_is_in_the_released_window(self):
        compaction = self.result["compaction"]
        self.assertEqual(compaction["compactions_in_window"], 1)
        self.assertEqual([b["index"] for b in compaction["boundaries"]], [1310, 1314])
        self.assertEqual(compaction["boundaries"][0]["cycles_budgeted"], 10)

    def test_sequence_metrics_are_pinned(self):
        sequence = self.result["sequence"]
        self.assertAlmostEqual(sequence["gap_seconds"]["median"], 15.402147)
        self.assertAlmostEqual(sequence["gap_seconds"]["p95"], 51.434563)
        self.assertEqual(sequence["gap_seconds"]["at_least_300"], 0)
        self.assertEqual(
            sequence["longest_same_tool_run"],
            {
                "tool": "terminal",
                "calls": 36,
                "start_index": 2069,
                "end_index": 2120,
            },
        )
        self.assertEqual(sequence["tool_transitions"]["terminal->terminal"], 577)

    def test_compaction_window_changes_are_pinned(self):
        windows = self.result["sequence"]["compaction_windows"]
        before = windows["before_first_compaction"]
        after = windows["after_first_compaction"]
        self.assertEqual(before["tool_calls"], 823)
        self.assertEqual(after["tool_calls"], 537)
        self.assertEqual(before["records_with_marker"]["pypi"], 137)
        self.assertEqual(after["records_with_marker"]["pypi"], 206)
        self.assertEqual(before["inline_redaction_markers"], 1840)
        self.assertEqual(after["inline_redaction_markers"], 757)

    def test_record_size_and_marker_summaries_are_pinned(self):
        self.assertEqual(self.result["record_sizes"]["TextMessage"]["p95_bytes"], 4088)
        self.assertEqual(self.result["record_sizes"]["ToolMessage"]["p95_bytes"], 3338)
        self.assertEqual(self.result["markers"]["records_with_marker"]["pypi"], 345)
        self.assertEqual(
            self.result["markers"]["record_cooccurrence"]["pypi+simulation"],
            21,
        )


if __name__ == "__main__":
    unittest.main()
