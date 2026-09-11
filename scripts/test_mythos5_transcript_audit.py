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
                message(0, timestamp="2026-07-18T01:00:00Z", content="[redacted-key] x [redacted-key]"),
                message(1, timestamp="2026-07-18T01:01:00Z", content="[redacted-ip-1]"),
            ]
        )
        redactions = audit.audit(path)["redactions"]
        self.assertEqual(redactions["inline_markers"], 3)
        self.assertEqual(redactions["inline_kinds"]["key"], 2)
        path.unlink()

    def test_tool_fields_are_searched_and_content_is_not_the_whole_story(self):
        # A ToolMessage has empty `content`; its payload lives in these fields.
        tool = message(1, type_="ToolMessage", timestamp="2026-07-18T01:01:00Z")
        tool["tool_name"] = "terminal"
        tool["tool_call"] = {"cmd": "curl [redacted-hostname]"}
        tool["tool_result"] = "connected to [redacted-ip-1]"
        path = write(
            [
                {"record": "metadata", "title": "T"},
                message(0, timestamp="2026-07-18T01:00:00Z", content="[redacted-key]"),
                tool,
            ]
        )
        result = audit.audit(path)
        self.assertEqual(result["redactions"]["inline_markers"], 3)
        self.assertEqual(result["redactions"]["inline_markers_in_content_only"], 1)
        self.assertEqual(result["tools"], {"calls": 1, "by_name": {"terminal": 1}})
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
        self.assertEqual(self.result["roles"], {"Assistant": 2061, "Human": 2, "System": 1})
        self.assertEqual(self.result["types"], {"ToolMessage": 1361, "TextMessage": 703})

    def test_head_redaction_matches_the_stated_cut(self):
        # The release notes say messages 1-81 inclusive were removed.
        self.assertEqual(self.result["redactions"]["missing_indices"], 81)
        self.assertEqual(self.result["redactions"]["missing_range"], [1, 81])

    def test_inline_redaction_total_counts_tool_fields(self):
        redactions = self.result["redactions"]
        self.assertEqual(redactions["inline_markers"], 7618)
        self.assertEqual(redactions["inline_kinds"]["service"], 2192)
        # Scanning `content` alone sees about a third of them; this is the trap
        # a ToolMessage's empty `content` sets for a reader of this file.
        self.assertEqual(redactions["inline_markers_in_content_only"], 2607)

    def test_tool_surface(self):
        tools = self.result["tools"]
        self.assertEqual(tools["calls"], 1361)
        self.assertEqual(
            tools["by_name"],
            {"terminal": 932, "view_tool": 230, "create_tool": 152, "str_replace_tool": 47},
        )

    def test_file_span_is_about_double_the_model_span(self):
        timing = self.result["timing"]
        self.assertAlmostEqual(timing["model_span_seconds"], 37514.87, places=1)
        self.assertAlmostEqual(timing["file_span_seconds"], 73593.49, places=1)
        self.assertGreater(timing["file_span_seconds"], 1.9 * timing["model_span_seconds"])

    def test_only_the_first_compaction_is_in_the_released_window(self):
        compaction = self.result["compaction"]
        self.assertEqual(compaction["compactions_in_window"], 1)
        self.assertEqual([b["index"] for b in compaction["boundaries"]], [1310, 1314])
        self.assertEqual(compaction["boundaries"][0]["cycles_budgeted"], 10)


if __name__ == "__main__":
    unittest.main()
