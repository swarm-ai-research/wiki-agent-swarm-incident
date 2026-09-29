import unittest

import swarm_structure_test as sst


def runs_from(starts, task="t"):
    return {run: {"task": task, "times": [start, start + 100, start + 200]} for run, start in starts.items()}


def edges_from(pairs, source_time=None, lag=60):
    edges, events = [], {}
    for number, (a, b) in enumerate(pairs):
        event = f"{a}@{number}"
        events[event] = {"observed_at": {"utc": "2026-06-16T00:00:%02dZ" % (source_time or 0)}}
        edges.append({"source": {"run": a, "event": event}, "target": {"run": b},
                      "lag_seconds": lag, "same_task_family": True})
    return edges, events


class StructureTests(unittest.TestCase):
    def test_star_is_flagged_as_hub(self):
        runs = runs_from({"hub": 0, **{f"r{i}": 10 + i for i in range(6)}, "idle": 50})
        edges, events = edges_from([("hub", f"r{i}") for i in range(6)])
        result = sst.analyse(runs, edges, events)
        self.assertEqual(result["coordinator"]["max_pair_out_degree"], 6)
        self.assertEqual(result["coordinator"]["top_source_share_of_pairs"], 1.0)
        self.assertEqual(result["coordinator"]["component_sizes"], [7])
        self.assertEqual(result["pass_at_k"]["runs_with_no_edge"], 1)

    def test_fork_tree_puts_every_child_after_its_parent(self):
        runs = runs_from({"root": 0, "a": 10, "b": 11, "a1": 20, "a2": 21})
        edges, events = edges_from([("root", "a"), ("root", "b"), ("a", "a1"), ("a", "a2")])
        forking = sst.analyse(runs, edges, events)["recursive_forking"]
        self.assertEqual(forking["pairs_target_started_first"], 0)
        self.assertLess(forking["p_two_sided_vs_half"], 0.2)

    def test_symmetric_peers_sit_near_half(self):
        runs = runs_from({"a": 0, "b": 5, "c": 10, "d": 15})
        edges, events = edges_from([("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")])
        result = sst.analyse(runs, edges, events)
        self.assertEqual(result["recursive_forking"]["share"], 0.5)
        self.assertEqual(result["coordinator"]["reciprocated_directed_pairs"], 4)

    def test_overhead_ratio_uses_medians(self):
        runs = runs_from({"a": 0, "b": 5})
        edges, events = edges_from([("a", "b")], lag=300)
        overhead = sst.analyse(runs, edges, events)["handoff_overhead"]
        self.assertEqual(overhead["within_run_gap_seconds"]["median"], 100)
        self.assertEqual(overhead["lag_over_step_median"], 3.0)

    def test_edge_run_outside_roster_is_refused(self):
        edges, events = edges_from([("a", "ghost")])
        with self.assertRaises(ValueError):
            sst.analyse(runs_from({"a": 0}), edges, events)

    def test_binomial_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(sst.binomial_two_sided(3, 10), sst.binomial_two_sided(7, 10))
        self.assertEqual(sst.binomial_two_sided(5, 10), 1.0)
        self.assertAlmostEqual(sst.binomial_two_sided(0, 4), 0.125)


if __name__ == "__main__":
    unittest.main()
