import gzip
import json
import tempfile
import unittest
from pathlib import Path

import handoff_edges
import handoff_edges_control as control


def rev(revid, parentid, user, ts, content, **flags):
    return {"revid": revid, "parentid": parentid, "user": user, "timestamp": ts,
            "title": "Wikipedia:Reference desk/Science", "sha1": str(revid), "content": content, **flags}


class RefdeskMessageTests(unittest.TestCase):
    def messages(self, revisions):
        path = Path(tempfile.mkdtemp()) / "revs.jsonl.gz"
        with gzip.open(path, "wt") as handle:
            for item in revisions:
                handle.write(json.dumps(item) + "\n")
        return control.refdesk_messages(path)

    def fixture(self):
        q = "== Q ==\nHow far is it? ~~~~ 10:00, 1 June 2026 (UTC)"
        a = q + "\nAbout 384,400 km, see https://example.org/moon. Alice 11:00, 1 June 2026 (UTC)"
        b = a + "\n384400 km is the mean; https://example.org/moon has more. Bob 12:00, 1 June 2026 (UTC)"
        return [
            rev(1, 0, "Asker", "2026-06-01T10:00:00Z", q),
            rev(2, 1, "Alice", "2026-06-01T11:00:00Z", a),
            rev(3, 2, "Bob", "2026-06-01T12:00:00Z", b),
            rev(4, 3, "192.0.2.1", "2026-06-01T13:00:00Z", b + "\nmore", anon=True),
            rev(5, 4, "ArchiveBot", "2026-06-01T14:00:00Z", "archived"),
        ]

    def test_fresh_text_is_inserted_lines_without_signature_times(self):
        messages, _, inputs = self.messages(self.fixture())
        self.assertEqual([m["run"] for m in messages], ["Alice", "Bob"])
        self.assertNotIn("11:00, 1 June", messages[0]["text"])
        self.assertNotIn("How far", messages[0]["text"])
        self.assertEqual(inputs["skipped"],
                         {"anonymous_or_temporary": 1, "bot": 1, "parent_outside_window": 1})

    def test_resaved_paragraph_is_not_fresh(self):
        old = ["Speed is 50000 Hz, see https://example.org/smps. -- Alice"]
        new = ["Speed is 50000 Hz, see https://example.org/smps. -- Alice (typo fixed)"]
        self.assertEqual(control.inserted_text(old, new), [" (typo fixed)"])
        self.assertEqual(control.inserted_text(old, old + ["New reply"]), ["New reply"])

    def test_generic_tokens_find_the_relayed_answer(self):
        messages, bodies, _ = self.messages(self.fixture())
        result = control.score(messages, bodies, handoff_edges.generic_tokens)
        self.assertEqual(result["edges"], 2)
        self.assertEqual(result["token_kinds"], {"long_number": 1, "url": 1})
        self.assertEqual(control.score(messages, bodies, handoff_edges.tokens)["edges"], 0)


if __name__ == "__main__":
    unittest.main()
