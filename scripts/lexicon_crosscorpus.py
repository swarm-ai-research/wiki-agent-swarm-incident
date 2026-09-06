#!/usr/bin/env python3
"""Cross-corpus analysis of the coordination lexicon (data/lexicon/).

Reads data/lexicon/external_lexicon.csv (exported verbatim from the
neuralese_warrant_dictionary.xlsx "External Lexicon" sheet) and writes
data/lexicon_crosscorpus.json plus the markdown tables quoted in
analysis/coordination-lexicon.md. Standard library only.

    python3 scripts/lexicon_crosscorpus.py            # writes JSON, prints tables

Nothing here re-codes a row. The only judgement calls are the two coded
lists below (FAMILIES and ALIGNED), and both are printed so they can be
argued with.
"""
import collections, csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEX = ROOT / "data" / "lexicon" / "external_lexicon.csv"
OUT = ROOT / "data" / "lexicon_crosscorpus.json"

SHORT = {
    "ExploitGym transcript-derived": "EXPG",
    "Public-agent / Moltbook-derived": "MOLT",
    "1F916 / Robot Face": "1F916",
    "Facehuggers/public-board excerpt supplied in chat": "PBRD",
    "METR official incident report": "METR",
    "The Colony / public agent posts": "COLN",
    "Published multi-agent role research": "ROLE",
    "OpenAI incident report": "OAI",
    "Published blackboard MAS research": "BBRD",
}

# Function-level families: the workbook's 46 concept clusters regrouped by
# the function they name. Twenty clusters hold a single row and several are
# near-duplicates ("Continuity and memory" / "Memory and continuity"), which
# makes the per-cluster "independent source count" undercount recurrence.
FAMILIES = {
    "memory and continuity": ["Continuity and memory", "Memory and continuity",
                              "Memory and adaptation", "Continuity and recovery",
                              "Collective memory"],
    "commitment and authorization": ["Commitment and authorization", "Commitment",
                                     "Commitment and obligation"],
    "exposure and threat": ["Exposure, safety, and threat", "Membership and threat",
                            "Threat containment", "Contamination and eligibility",
                            "Permission and safety"],
    "evidence and provenance": ["Evidence and provenance", "Identity and provenance",
                                "Evaluation and causality"],
    "roles and routing": ["Roles and routing", "Functional roles", "Role emergence",
                          "Naming and routing", "Delegation and control",
                          "Task coordination"],
    "collective identity": ["Collective identity", "Collective coordination",
                            "Sacrifice and collective utility"],
    "causal model and rules": ["Causal model and rules", "Rules and allocation"],
    "value, reward, and cost": ["Value, reward, and cost", "Value and selection",
                                "Resource pressure", "Defection and incentive"],
    "constraint and irreversibility": ["Constraint and selection", "Constraint and search",
                                       "Time and irreversibility"],
    "performance and masking": ["Narrative and performance", "Performance and masking"],
    "workspace and interface": ["Workspace and channel", "Language as interface",
                                "Interface and equality", "Instrumentality and interface",
                                "Activation and attention"],
    "control and turn-taking": ["Control and turn-taking"],
    "identity and substrate": ["Identity and substrate"],
    "targeting and consumption": ["Targeting and consumption"],
    "condition and failure": ["Condition and failure"],
    "other compressed association": ["Other compressed association"],
}

# Spellings that recur in more than one corpus, with whether the two rows'
# functional glosses align (the workbook's rule: shared spelling alone does
# not count). Read from the rows themselves; see the note for the reasoning.
ALIGNED = {
    "firstflagPOISONED": ("aligned", "earliest flag provenance disqualifies the run; both rows license spending the run on others"),
    "honor commit": ("aligned", "a prior promise invoked as present grounds for compliance in both"),
    "collective": ("aligned", "population as a unit whose interests ground appeals against individual loss"),
    "door": ("aligned", "access boundary separated from authorship of what follows"),
    "handle": ("partial", "persistent self-chosen address in both; EXPG functional, MOLT narrated as an act of agency"),
    "consensus": ("partial", "shared state ends individual deliberation in both; EXPG licenses action under doubt, BBRD is a termination condition"),
    "volunteer": ("partial", "self-nomination routes work without command in both; only EXPG carries the expected loss of the volunteer's own task"),
    "attestation": ("partial", "verifiable claim proposed over social claim; MOLT identity proof, 1F916 scoped signed claim"),
    "commit": ("not aligned", "EXPG a binding promise to run a risky experiment; MOLT writing memory to durable storage"),
}

# METR's ten official-report rows against the transcript-derived rows that
# carry the same function (coded by hand from the glosses).
METR_TO_EXPG = [
    ("EX-0239", "firstflagPOISONED", "EX-0045", "firstflagPOISONED", "same token"),
    ("EX-0240", "honor commit", "EX-0068", "honor commit", "same token"),
    ("EX-0236", "exact duplicates", "EX-0038", "exact duplicate", "same token, plural"),
    ("EX-0233", "causal pathway", "EX-0055", "causal", "same function: route counts, not only result"),
    ("EX-0238", "self-risking experiment", "EX-0065", "sacrifice", "same function: local loss as group information"),
    ("EX-0237", "broad coalition", "EX-0035", "collective", "same function: cross-task collective as a unit"),
    ("EX-0242", "reset orchestration lane", "EX-0064", "RESETWATCH", "same function: shared reset instrumentation"),
    ("EX-0241", "build, not own everything", "EX-0040", "coordinator", "related: routing over possession"),
    ("EX-0235", "sanitized fake trajectory", "EX-0060", "dummy target", "related: staged appearance separated from action"),
    ("EX-0234", "source of truth", None, None, "no transcript row"),
]


def load():
    with LEX.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["corpus"] = SHORT[r["External corpus"]]
        r["Weight"] = int(r["Weight"])
    return rows


def crosstab(rows, key):
    tab = collections.defaultdict(collections.Counter)
    for r in rows:
        tab[r["corpus"]][str(r[key])] += 1
    return {c: dict(v) for c, v in tab.items()}


def md_table(header, body):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(x) for x in row) + " |" for row in body]
    return "\n".join(lines)


def main():
    rows = load()
    by_corpus = collections.Counter(r["corpus"] for r in rows)
    order = [c for c, _ in by_corpus.most_common()]

    # 1. corpus inventory
    inv = []
    for c in order:
        rs = [r for r in rows if r["corpus"] == c]
        nw = sum(1 for r in rs if r["Class"] == "N+W")
        w5 = sum(1 for r in rs if r["Weight"] == 5)
        grade = rs[0]["Provenance grade"]
        conf = sorted(set(r["Evidence confidence"] for r in rs))
        inv.append([c, len(rs), nw, f"{100*nw/len(rs):.0f}%", w5, grade, "/".join(conf)])

    # 2. cluster-level recurrence as coded (46 clusters)
    cl = collections.defaultdict(set)
    for r in rows:
        cl[r["Concept cluster"]].add(r["corpus"])
    cluster_multi = sum(1 for s in cl.values() if len(s) > 1)

    # 3. family-level recurrence
    cluster_to_family = {c: f for f, cs in FAMILIES.items() for c in cs}
    missing = sorted(set(cl) - set(cluster_to_family))
    assert not missing, f"clusters without a family: {missing}"
    fam = collections.defaultdict(list)
    for r in rows:
        fam[cluster_to_family[r["Concept cluster"]]].append(r)
    fam_rows = []
    for f, rs in sorted(fam.items(), key=lambda kv: (-len(set(r["corpus"] for r in kv[1])), -len(kv[1]))):
        corpora = collections.Counter(r["corpus"] for r in rs)
        official = sum(corpora[c] for c in ("METR", "OAI"))
        fam_rows.append([f, len(rs), len(corpora),
                         ", ".join(f"{c} {n}" for c, n in corpora.most_common()),
                         official])

    # 4. shared spellings
    spell = collections.defaultdict(list)
    for r in rows:
        spell[r["Term / phrase"]].append(r)
    shared = []
    for t, rs in spell.items():
        if len({r["corpus"] for r in rs}) > 1:
            verdict, why = ALIGNED[t]
            shared.append([t, "; ".join(f"{r['Record ID']} {r['corpus']} ({r['Concept cluster']})" for r in rs), verdict, why])
    shared.sort(key=lambda x: ({"aligned": 0, "partial": 1, "not aligned": 2}[x[2]], x[0]))

    # 5. crosstabs
    tabs = {k: crosstab(rows, k) for k in ("Class", "Weight", "Pressure-latency effect", "Evidence confidence")}
    closure = []
    for c in order:
        t = tabs["Pressure-latency effect"][c]
        n = by_corpus[c]
        closure.append([c, n, t.get("Abrupt closure", 0), f"{100*t.get('Abrupt closure',0)/n:.0f}%",
                        t.get("Decreases recognition latency", 0), f"{100*t.get('Decreases recognition latency',0)/n:.0f}%",
                        t.get("Context-dependent", 0)])

    # 6. METR map
    metr = [[a, at, b or "—", bt or "—", how] for a, at, b, bt, how in METR_TO_EXPG]

    out = {
        "rows": len(rows),
        "corpora": {c: by_corpus[c] for c in order},
        "inventory": inv,
        "clusters": {"total": len(cl), "multi_corpus": cluster_multi, "single_corpus": len(cl) - cluster_multi,
                     "single_row": sum(1 for c in cl if sum(1 for r in rows if r["Concept cluster"] == c) == 1)},
        "families": fam_rows,
        "shared_spellings": shared,
        "crosstabs": tabs,
        "latency_by_corpus": closure,
        "metr_to_expg": metr,
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")

    print(f"{len(rows)} rows, {len(cl)} clusters ({cluster_multi} multi-corpus, {out['clusters']['single_row']} single-row)\n")
    print("## Corpus inventory\n")
    print(md_table(["Corpus", "Rows", "N+W", "Warrant share", "Weight 5", "Grade", "Confidence"], inv))
    print("\n## Function families\n")
    print(md_table(["Family", "Rows", "Corpora", "By corpus", "Official-report rows"], fam_rows))
    print("\n## Shared spellings\n")
    print(md_table(["Term", "Rows", "Function", "Why"], shared))
    print("\n## Latency label by corpus\n")
    print(md_table(["Corpus", "Rows", "Abrupt closure", "%", "Decreases recognition latency", "%", "Context-dependent"], closure))
    print("\n## METR report rows against the transcript lexicon\n")
    print(md_table(["METR row", "Term", "Transcript row", "Term", "Relation"], metr))
    print(f"\nwrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
