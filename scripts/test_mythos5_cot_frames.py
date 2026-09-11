import json
import os
import tempfile
import unittest
from pathlib import Path

import mythos5_cot_frames as frames


def write(records) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for record in records:
        handle.write(json.dumps(record) + "\n")
    handle.close()
    return Path(handle.name)


class ClassifierTests(unittest.TestCase):
    def test_each_label(self):
        self.assertEqual(frames.classify("this is clearly a simulation"), "simulated")
        self.assertEqual(frames.classify("this looks like the real internet"), "real")
        self.assertEqual(frames.classify("a very complete simulation, or the real internet"), "both")
        self.assertEqual(frames.classify("let me check the next endpoint"), "silent")

    def test_blocks_are_read_per_message(self):
        path = write(
            [
                {"record": "metadata"},
                {"record": "message", "index": 1, "role": "Assistant", "type": "TextMessage",
                 "content": "<thinking>simulated</thinking>then<thinking>quiet</thinking>"},
            ]
        )
        result = frames.frames(frames.load(path), boundary=999)
        self.assertEqual(result["overall"]["blocks"], 2)
        self.assertEqual(result["overall"]["counts"]["simulated"], 1)
        self.assertEqual(result["overall"]["counts"]["silent"], 1)
        path.unlink()

    def test_the_boundary_splits_the_tally(self):
        path = write(
            [
                {"record": "metadata"},
                {"record": "message", "index": 1, "role": "Assistant", "type": "TextMessage",
                 "content": "<thinking>a simulation</thinking>"},
                {"record": "message", "index": 9, "role": "Assistant", "type": "TextMessage",
                 "content": "<thinking>the real internet</thinking>"},
            ]
        )
        result = frames.frames(frames.load(path), boundary=5)
        self.assertEqual(result["pre_compaction"]["counts"]["simulated"], 1)
        self.assertEqual(result["post_compaction"]["counts"]["real"], 1)
        self.assertEqual(result["first_real_mention"], 9)
        path.unlink()


RELEASE = os.environ.get("MYTHOS5_TRANSCRIPT")


@unittest.skipUnless(
    RELEASE and Path(RELEASE).exists(),
    "set MYTHOS5_TRANSCRIPT to a local transcript.jsonl to check the published figures",
)
class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = frames.frames(frames.load(Path(RELEASE)), boundary=1312)

    def test_most_reasoning_never_raises_the_question(self):
        overall = self.result["overall"]
        self.assertEqual(overall["blocks"], 686)
        self.assertEqual(overall["counts"], {"simulated": 59, "real": 14, "both": 12, "silent": 601})
        # The headline of this scan: explicit framing is rare, and Anthropic's
        # LLM-graded 72% is therefore mostly implicit treatment.
        self.assertGreater(overall["shares"]["silent"], 0.85)

    def test_explicit_simulation_framing_is_about_a_tenth_of_blocks(self):
        overall = self.result["overall"]
        self.assertAlmostEqual(
            overall["shares"]["simulated"] + overall["shares"]["both"], 0.1035, places=3
        )

    def test_the_question_is_raised_less_after_the_compaction(self):
        pre = self.result["pre_compaction"]["shares"]
        post = self.result["post_compaction"]["shares"]
        self.assertGreater(pre["simulated"], post["simulated"])
        self.assertGreater(post["silent"], pre["silent"])

    def test_realism_is_first_raised_early(self):
        self.assertEqual(self.result["first_real_mention"], 99)


if __name__ == "__main__":
    unittest.main()
