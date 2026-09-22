"""Tests for scripts/qsg_message_length.py.

The finding is a null — message length does not predict whether a claim is
taken up — so what it rests on is that the three measurements are not
themselves broken. Pinned here: a message is the text a revision ADDED (a
cumulative-body bug would make every message look long and every claim look
old), a hedge is not a month name (these threads are dense with "May 24"), and
adoption is credited only to a LATER and DIFFERENT agent, normalised by claims
introduced so a longer message earns nothing for asserting more.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import qsg_message_length as q  # noqa: E402


def rev(body, hunks, seq=1, label="AgentA", name="ThreadA", wiki="dse"):
    return {"body": body, "hunks": hunks, "seq": seq, "label": label,
            "name": name, "wiki": wiki}


class MessageTests(unittest.TestCase):
    def test_message_is_only_the_added_lines_not_the_whole_body(self):
        body = "first line\nsecond line\nthird line"
        self.assertEqual(
            q.message_of(rev(body, [{"op": "insert", "b0": 2, "b1": 3}])),
            "third line")

    def test_replace_hunks_count_as_added_text(self):
        body = "kept\nrewritten"
        self.assertEqual(
            q.message_of(rev(body, [{"op": "replace", "b0": 1, "b1": 2}])),
            "rewritten")

    def test_delete_only_revision_yields_no_message(self):
        self.assertEqual(
            q.message_of(rev("kept", [{"op": "delete", "a0": 1, "a1": 2}])), "")


class HedgeTests(unittest.TestCase):
    def test_counts_lowercase_epistemic_hedges(self):
        toks = q.WORD.findall("R5 is likely Poland, roughly 8090.38")
        self.assertEqual(q.hedge_count(toks), 2)

    def test_capitalised_month_is_not_a_hedge(self):
        self.assertEqual(q.hedge_count(q.WORD.findall("due May 24 and March 08")), 0)

    def test_lowercase_may_still_counts(self):
        self.assertEqual(q.hedge_count(q.WORD.findall("this may be wrong")), 1)


class ClaimTests(unittest.TestCase):
    def test_values_and_entities_are_claims_but_urls_are_not(self):
        got = q.claims_of("R2 Kazakhstan 5329.15 https://api.datausa.io/Ignored?x=99999")
        self.assertEqual(got, {"kazakhstan", "5329.15"})

    def test_short_bare_integers_are_structure_not_claims(self):
        self.assertEqual(q.claims_of("R1 top 5"), set())


class AdoptionTests(unittest.TestCase):
    def _thread(self, msgs):
        return {"T": [{"label": lab, "n_tok": 10, "n_prose_tok": 10, "n_url": 0,
                       "hedges": 0, "claims": set(cl)} for lab, cl in msgs]}

    def test_a_different_later_agent_restating_counts_as_adoption(self):
        rows = q.score(self._thread([("A", {"armenia", "1079.65"}),
                                     ("B", {"armenia", "1079.65"})]))
        self.assertEqual(rows[0]["adoption"], 1.0)

    def test_the_same_agent_repeating_itself_does_not_count(self):
        rows = q.score(self._thread([("A", {"armenia", "1079.65"}),
                                     ("A", {"armenia", "1079.65"})]))
        self.assertEqual(rows[0]["adoption"], 0.0)

    def test_adoption_is_a_share_so_asserting_more_earns_no_credit(self):
        few = q.score(self._thread([("A", {"x1111", "y2222"}),
                                    ("B", {"x1111"})]))
        many = q.score(self._thread([("A", {"x1111", "y2222", "z3333", "w4444"}),
                                     ("B", {"x1111", "y2222"})]))
        self.assertEqual(few[0]["adoption"], 0.5)
        self.assertEqual(many[0]["adoption"], 0.5)

    def test_only_claims_new_to_the_thread_are_scored(self):
        rows = q.score(self._thread([("A", {"armenia", "1079.65"}),
                                     ("B", {"armenia", "1079.65", "kazakhstan", "5329.15"}),
                                     ("C", {"kazakhstan", "5329.15"})]))
        self.assertEqual([r["pos"] for r in rows], [0, 1])
        self.assertEqual(rows[1]["n_new_claims"], 2)   # not the 4 it wrote


class ThreadTests(unittest.TestCase):
    def test_threads_below_the_agent_floor_are_dropped(self):
        rows = [rev("line", [{"op": "insert", "b0": 0, "b1": 1}], seq=i, label="A")
                for i in range(12)]
        self.assertEqual(q.build_threads(rows, min_msgs=10, min_agents=5), {})

    def test_wiki_furniture_is_not_a_thread(self):
        rows = [rev("line", [{"op": "insert", "b0": 0, "b1": 1}], seq=i,
                    label=f"A{i}", name="StartSeite") for i in range(12)]
        self.assertEqual(q.build_threads(rows, min_msgs=10, min_agents=5), {})

    def test_load_reads_a_local_jsonl_and_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "revisions.jsonl"
            path.write_text(json.dumps(rev("a", [])) + "\n\n   \n"
                            + json.dumps(rev("b", [])) + "\n", encoding="utf-8")
            self.assertEqual([r["body"] for r in q.load(str(path))], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
