import unittest
from datetime import datetime

import sentinellabs_hf_join as join


def capture(repo_id, created, changes):
    user = repo_id.split("/")[0]
    return {"accounts": {user: {"repos": [{
        "kind": "spaces", "id": repo_id, "info": {"createdAt": created},
        "commits": [{"date": c["date"]} for c in changes], "file_changes": changes}]}}}


class ClaimTests(unittest.TestCase):
    def test_matches_only_the_exact_second(self):
        cap = capture("Nyx9/netproxy17", "2026-05-26T20:48:33.000Z", [
            {"date": "2026-05-26T20:49:54.000Z", "added": {"app.py": {}}, "changed": {}},
        ])
        rows = {r["claim"]: r["verdict"] for r in join.check_claims(cap)}
        self.assertEqual(rows["Nyx9/netproxy17 created"], "matches")
        self.assertEqual(rows["Nyx9/netproxy17 relay code (OpenAI: first proxy deployment, 20:49)"], "not found")

    def test_unreported_skips_report_repos(self):
        cap = capture("0Time/probeoid", "2026-05-26T19:06:32.000Z", [
            {"date": "2026-05-26T19:06:32.000Z", "added": {".gitattributes": {}}, "changed": {}},
        ])
        self.assertEqual([r["repo"] for r in join.unreported(cap)], ["spaces/0Time/probeoid"])


class WindowTests(unittest.TestCase):
    def test_observed_against_uniform_expectation(self):
        t = datetime(2026, 5, 26, 20, 4, 11)
        records = [(datetime(2026, 5, 26, 20, 3), "a"), (datetime(2026, 5, 26, 20, 30), "b")]
        row = join.window_counts(records, [("hello", t)], minutes=5, baseline_hours=12)[0]
        self.assertEqual(row["observed"], 1)
        self.assertEqual(row["venues"], {"a": 1})
        self.assertEqual(row["expected"], round(2 * 10 / 1440, 1))


if __name__ == "__main__":
    unittest.main()
