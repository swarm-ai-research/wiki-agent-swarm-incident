import sqlite3
import unittest

import dse_teardown_order as dto


@unittest.skipUnless(
    dto.DB.exists(),
    f"incident bundle not present at {dto.DB}; fetch it with verify_termina_snapshot.py",
)
class TeardownOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = sqlite3.connect(f"file:{dto.DB}?mode=ro", uri=True)
        cls.ordering = dto.ordering(cls.connection, "dse", seed=0)
        cls.evasion = dto.evasion(cls.connection, "dse")

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_pinned_snapshot_teardown_scope(self):
        self.assertEqual(self.ordering["deletes"], 5217)
        self.assertEqual(
            self.ordering["window"], ["2026-06-04T10:53:40Z", "2026-07-14T13:56:54Z"]
        )

    def test_deletion_is_locally_sorted_but_not_a_global_sweep(self):
        # A global A-Z sweep would put the ascending share near 1.0; chance is 0.5.
        self.assertLess(self.ordering["ascending_pair_share"], 0.7)
        self.assertGreater(self.ordering["ascending_pair_share"], 0.55)
        # Long ascending runs are still far beyond what shuffling the same bursts gives.
        self.assertGreater(
            self.ordering["longest_ascending_run"],
            10 * self.ordering["null_longest_ascending_run"],
        )
        self.assertGreater(
            self.ordering["runs_of_5_or_more"],
            3 * self.ordering["null_runs_of_5_or_more"],
        )

    def test_z_prefixed_pages_gained_no_survival_advantage(self):
        self.assertEqual(self.evasion["z_prefixed"]["survival_rate"], 0.0)
        self.assertEqual(self.evasion["zzz_anywhere"]["survival_rate"], 0.0)
        self.assertGreater(self.evasion["all"]["survival_rate"], 0.0)

    def test_bursts_shorter_than_the_floor_are_dropped(self):
        rows = [("2026-06-04T10:00:00Z", "A"), ("2026-06-04T10:00:01Z", "B")]
        self.assertEqual(dto._bursts(rows), [])


if __name__ == "__main__":
    unittest.main()
