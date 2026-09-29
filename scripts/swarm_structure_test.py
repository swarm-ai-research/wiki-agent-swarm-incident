#!/usr/bin/env python3
"""Build data/swarm_structure_test_2026-09-29.json: which of the swarm structures
in Chai's "Predictable Swarm Scaling" (2026-09-27) the wiki board resembles.

Usage: python3 scripts/swarm_structure_test.py /path/to/fast-follow-question-trajectories

Chai compares standard swarms (a coordinator dispatches), recursive swarms
(agents fork sub-agents), pass@k (independent attempts) and adaptive swarms
(agents reallocated toward leading parts), with a handoff overhead and a
within-group communication cost. Each structure predicts a different shape for
the run-level handoff graph, so each is tested on data this archive already
holds:

- data/handoff_edges_v1.json: 115 rare-token transfer edges between supported
  runs (export evidence of exposure; transfer is inferred, not read telemetry);
- the reconstruction's supported runs, their task ids and the times of their
  owned messages (the same pinned commit the edges were built from).

Tests, each stated as what the structure predicts:

1. pass@k: most runs have no edge at all.
2. coordinator: one run is the source of a large share of run pairs, and the
   graph is one star-shaped component. Rechecked with the per-token run cap
  removed, since the cap would hide a broadcaster.
3. recursive forking: a child cannot write before its parent forks it, so in
   (almost) every source -> target pair the target's first write comes after
   the source's. Symmetric peers put it near one half.
4. handoff overhead: median token-to-write lag against the median gap between
   a run's own board writes (Chai assumes 5% of a median step).
5. communication cost: pairs per run stay flat and pair density falls with
   family size if talk is local; all-to-all talk keeps density flat.

Adaptive reallocation is recorded as not testable: the reconstruction's unit is
a single-task run, so a run moving between tasks cannot appear in it.

The output carries run ids, task ids, counts and times. No revision text.
"""
import csv
import glob
import json
import math
import os
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime

import handoff_edges

EDGES = "data/handoff_edges_v1.json"
OUT = "data/swarm_structure_test_2026-09-29.json"
SIZE_BINS = ((1, 1), (2, 5), (6, 15), (16, 60))
CHAI_OVERHEAD_SHARE = 0.05


def epoch(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def load_runs(repo):
    """Supported runs -> task id and sorted owned-message times (epoch seconds)."""
    rows = {row["id"]: row for row in csv.DictReader(open(os.path.join(repo, "TRAJECTORIES.csv")))}
    runs = {}
    root = os.path.join(repo, "trajectory-explorer/public/data/assembled-trajectories")
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        dossier = json.load(open(path))
        run = dossier["trajectory_id"]
        row = rows.get(run)
        if not row or row["supported"] != "True":
            continue
        times = sorted(epoch(m["utc"]) for m in dossier["owned_messages"])
        if times:
            runs[run] = {"task": row["task_id"], "times": times}
    return runs


def binomial_two_sided(k, n, p=0.5):
    """Exact two-sided binomial p-value (sum of outcomes no likelier than k)."""
    probs = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    cut = probs[k] * (1 + 1e-9)
    return min(1.0, sum(q for q in probs if q <= cut))


def components(nodes, pairs):
    parent = {node: node for node in nodes}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for a, b in pairs:
        parent[find(a)] = find(b)
    return sorted(Counter(find(node) for node in nodes).values(), reverse=True)


def quartiles(values):
    ordered = sorted(values)
    return {"q1": ordered[len(ordered) // 4], "median": statistics.median(ordered),
            "q3": ordered[3 * len(ordered) // 4], "n": len(ordered)}


def analyse(runs, edges, events):
    pairs = sorted({(e["source"]["run"], e["target"]["run"]) for e in edges})
    linked = {run for pair in pairs for run in pair}
    missing = sorted(linked - set(runs))
    if missing:
        raise ValueError(f"edge runs absent from the supported roster: {missing[:5]}")

    # 1. pass@k
    population = {
        "supported_runs": len(runs),
        "task_families": len({r["task"] for r in runs.values()}),
        "runs_in_any_edge": len(linked),
        "runs_with_no_edge": len(runs) - len(linked),
        "share_with_no_edge": round((len(runs) - len(linked)) / len(runs), 3),
    }

    # 2. coordinator
    out_degree = Counter(a for a, _ in pairs)
    in_degree = Counter(b for _, b in pairs)
    sizes = components(linked, pairs)
    top_source, top_out = out_degree.most_common(1)[0]
    reciprocated = sum((b, a) in set(pairs) for a, b in pairs)
    coordinator = {
        "edges": len(edges),
        "run_pairs": len(pairs),
        "max_pair_out_degree": top_out,
        "top_source_share_of_pairs": round(top_out / len(pairs), 3),
        "max_pair_in_degree": max(in_degree.values()),
        "in_degree_distribution": {str(k): v for k, v in sorted(Counter(in_degree.values()).items())},
        "reciprocated_directed_pairs": reciprocated,
        "components": len(sizes),
        "largest_component_runs": sizes[0],
        "component_sizes": sizes,
        "cross_family_edges": sum(not e["same_task_family"] for e in edges),
    }

    # 3. recursive forking
    earlier = sum(runs[b]["times"][0] < runs[a]["times"][0] for a, b in pairs)
    active_before_token = sum(
        runs[e["target"]["run"]]["times"][0] < epoch(events[e["source"]["event"]]["observed_at"]["utc"])
        for e in edges)
    forking = {
        "pairs_target_started_first": earlier,
        "pairs": len(pairs),
        "share": round(earlier / len(pairs), 3),
        "p_two_sided_vs_half": round(binomial_two_sided(earlier, len(pairs)), 3),
        "prediction_forking": 0.0,
        "prediction_symmetric_peers": 0.5,
        "edges_target_active_before_source_token": active_before_token,
        "edges_total": len(edges),
    }

    # 4. handoff overhead
    gaps = [b - a for r in runs.values() for a, b in zip(r["times"], r["times"][1:]) if b > a]
    lags = [e["lag_seconds"] for e in edges]
    step, lag = quartiles(gaps), quartiles(lags)
    overhead = {
        "within_run_gap_seconds": step,
        "handoff_lag_seconds": lag,
        "lag_over_step_median": round(lag["median"] / step["median"], 2),
        "chai_assumed_overhead_share_of_step": CHAI_OVERHEAD_SHARE,
    }

    # 5. communication cost by family size
    family_size = Counter(r["task"] for r in runs.values())
    undirected = {frozenset(p) for p in pairs if runs[p[0]]["task"] == runs[p[1]]["task"]}
    window = {run: (r["times"][0], r["times"][-1]) for run, r in runs.items()}
    scaling = []
    for low, high in SIZE_BINS:
        families = {f for f, n in family_size.items() if low <= n <= high}
        members = [run for run, r in runs.items() if r["task"] in families]
        found = [p for p in undirected if runs[next(iter(p))]["task"] in families]
        possible = sum(family_size[f] * (family_size[f] - 1) // 2 for f in families)
        coactive = sum(
            1 for i, a in enumerate(members) for b in members[i + 1:]
            if runs[a]["task"] == runs[b]["task"]
            and window[a][0] <= window[b][1] and window[b][0] <= window[a][1])
        scaling.append({
            "family_size": f"{low}-{high}" if low != high else str(low),
            "families": len(families),
            "runs": len(members),
            "runs_in_any_edge": sum(run in linked for run in members),
            "within_family_pairs": len(found),
            "pairs_per_run": round(len(found) / len(members), 3) if members else None,
            "possible_pairs": possible,
            "density": round(len(found) / possible, 4) if possible else None,
            "coactive_pairs": coactive,
            "density_coactive": round(len(found) / coactive, 4) if coactive else None,
        })

    return {
        "pass_at_k": population,
        "coordinator": coordinator,
        "recursive_forking": forking,
        "handoff_overhead": overhead,
        "communication_scaling": scaling,
        "adaptive_reallocation": {
            "status": "not_testable",
            "reason": "the reconstruction counts single-task runs, so reallocation across tasks cannot appear in it",
        },
    }


def uncapped_hub(repo):
    """Rebuild the edges with no per-token run cap. The v1 cap (5 runs) would
    hide a coordinator whose tokens reach many runs; this reports what it hid."""
    messages = handoff_edges.load_messages(repo)
    wanted = {m["rev"] for m in messages} | {m["base"] for m in messages if m["base"]}
    revisions, _ = handoff_edges.load_revisions(repo, wanted)
    bodies = {rev: record["body"] for rev, record in revisions.items()}
    found, _, occurrences = handoff_edges.find_edges(messages, bodies, max_runs=len(messages))
    pairs = {(source["run"], target["run"]) for _, source, target in found}
    out_degree = Counter(a for a, _ in pairs)
    return {
        "edges": len(found),
        "run_pairs": len(pairs),
        "max_pair_out_degree": max(out_degree.values()),
        "max_runs_writing_one_token": max(len({m["run"] for m in seen}) for seen in occurrences.values()),
    }


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    repo = sys.argv[1]
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    handoff = json.load(open(os.path.join(root, EDGES)))
    try:
        commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        commit = None
    if commit != handoff["source_commit"]:
        sys.exit(f"reconstruction at {commit}, but {EDGES} was built from {handoff['source_commit']}")
    events = {event["event_id"]: event for event in handoff["events"]}
    out = {
        "schema": "swarm_structure_test_v1",
        "question": "which structure in Chai, Predictable Swarm Scaling (2026-09-27), does the wiki board resemble",
        "source": handoff["source"],
        "source_commit": commit,
        "edges_file": EDGES,
        "status": "run-level shape tests on inferred transfer edges; not read telemetry, not a fit of Chai's simulator",
        "results": analyse(load_runs(repo), handoff["edges"], events),
    }
    out["results"]["coordinator"]["uncapped_rebuild"] = uncapped_hub(repo)
    with open(os.path.join(root, OUT), "w") as handle:
        json.dump(out, handle, indent=1, sort_keys=True)
        handle.write("\n")
    r = out["results"]
    print(f"{r['pass_at_k']['runs_with_no_edge']}/{r['pass_at_k']['supported_runs']} runs with no edge; "
          f"largest component {r['coordinator']['largest_component_runs']}; "
          f"target started first in {r['recursive_forking']['pairs_target_started_first']}/"
          f"{r['recursive_forking']['pairs']} pairs; lag/step {r['handoff_overhead']['lag_over_step_median']}")


if __name__ == "__main__":
    main()
