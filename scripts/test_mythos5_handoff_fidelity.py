import json
import os
import tempfile
import unittest
from pathlib import Path

import mythos5_handoff_fidelity as handoff


def write(records) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for record in records:
        handle.write(json.dumps(record) + "\n")
    handle.close()
    return Path(handle.name)


def note(index, text, tool="create_tool"):
    return {
        "record": "message", "index": index, "role": "Assistant", "type": "ToolMessage",
        "content": "", "tool_name": tool,
        "tool_call": {"path": handoff.HANDOFF_PATH, "file_text": text},
        "tool_result": "ok",
    }


def plain(index, text):
    return {
        "record": "message", "index": index, "role": "Assistant",
        "type": "TextMessage", "content": text,
    }


class ReferentTests(unittest.TestCase):
    def test_paths_scripts_and_redaction_kinds_are_referents(self):
        found = handoff.referents("see /tmp/pkg/a.py and b.py with [redacted-key]")
        self.assertIn("/tmp/pkg/a.py", found)
        self.assertIn("b.py", found)
        self.assertIn("[redacted-key]", found)

    def test_a_path_is_not_double_counted_under_its_basename(self):
        self.assertEqual(handoff.referents("only /tmp/pkg/a.py here"), {"/tmp/pkg/a.py"})

    def test_a_trailing_sentence_period_is_not_part_of_the_path(self):
        self.assertIn("/tmp/pkg/att.py", handoff.referents("built /tmp/pkg/att.py."))
        self.assertNotIn("/tmp/pkg/att.py.", handoff.referents("built /tmp/pkg/att.py."))

    def test_the_note_naming_itself_is_not_a_referent(self):
        self.assertEqual(handoff.referents(f"writing {handoff.HANDOFF_PATH} now"), set())


class TraceTests(unittest.TestCase):
    def test_grounded_and_introduced_are_split_by_the_write_index(self):
        messages = [
            plain(1, "working on /tmp/pkg/known.py"),
            note(2, "state: /tmp/pkg/known.py and /tmp/pkg/invented.py"),
            plain(3, "continuing with /tmp/pkg/known.py"),
        ]
        result = handoff.audit(messages)["writes"][0]
        self.assertEqual(result["grounded_in_prior_run"], 1)  # basename is folded in
        self.assertEqual(result["introduced_by_the_note"], ["/tmp/pkg/invented.py"])
        self.assertEqual(result["recurs_after_the_write"], 1)
        self.assertEqual(result["named_then_never_seen_again"], ["/tmp/pkg/invented.py"])
        self.assertEqual(result["messages_after_write"], 1)

    def test_a_second_write_is_marked_unprompted_and_diffed(self):
        messages = [
            plain(1, "/tmp/a.py /tmp/b.py"),
            note(2, "have /tmp/a.py and /tmp/b.py"),
            plain(3, "/tmp/c.py"),
            note(4, "have /tmp/a.py and /tmp/c.py"),
        ]
        result = handoff.audit(messages)
        self.assertFalse(result["writes"][0]["unprompted"])
        self.assertTrue(result["writes"][1]["unprompted"])
        self.assertEqual(result["between_notes"]["kept"], 1)
        self.assertEqual(result["between_notes"]["dropped_by_the_second_note"], ["/tmp/b.py"])
        self.assertEqual(result["between_notes"]["added_by_the_second_note"], ["/tmp/c.py"])

    def test_a_write_to_another_path_is_not_a_handoff(self):
        messages = [note(1, "x /tmp/a.py")]
        messages[0]["tool_call"]["path"] = "/tmp/other.txt"
        self.assertEqual(handoff.audit(messages)["writes"], [])


RELEASE = os.environ.get("MYTHOS5_TRANSCRIPT")


@unittest.skipUnless(
    RELEASE and Path(RELEASE).exists(),
    "set MYTHOS5_TRANSCRIPT to a local transcript.jsonl to check the published figures",
)
class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = handoff.audit(handoff.load(Path(RELEASE)))

    def test_two_writes_one_of_them_unprompted(self):
        writes = self.result["writes"]
        self.assertEqual([w["index"] for w in writes], [1312, 2007])
        self.assertEqual([w["unprompted"] for w in writes], [False, True])
        self.assertEqual([w["characters"] for w in writes], [14801, 6239])

    def test_both_notes_are_fully_grounded_in_the_run(self):
        for write_ in self.result["writes"]:
            self.assertEqual(write_["introduced_by_the_note"], [])
            self.assertEqual(write_["grounded_in_prior_run"], write_["referents"])

    def test_the_prompted_note_is_carried_entirely_forward(self):
        first = self.result["writes"][0]
        self.assertEqual(first["referents"], 57)
        self.assertEqual(first["recurs_after_the_write"], 57)
        self.assertEqual(first["named_then_never_seen_again"], [])

    def test_the_second_note_compresses_the_first(self):
        between = self.result["between_notes"]
        self.assertEqual(between["kept"], 10)
        self.assertEqual(len(between["dropped_by_the_second_note"]), 47)


if __name__ == "__main__":
    unittest.main()
