#!/usr/bin/env python3
"""Test the QSG message-length prediction against DSEWiki revision bodies.

Tanaka's Quantized Simplex Gossip model (arXiv:2603.24676, "When Is Collective
Intelligence a Lottery?"; blog framing at physicsintelligence.org, 2026-09-05)
predicts that SHORTER inter-agent messages accelerate collective belief
collapse, by a stated mechanism: truncation strips the speaker's hedging, so
listeners update as if the claim were confident.

That is two separable claims and this script tests them separately:

  M (mechanism)  shorter messages carry proportionally less hedging
  O (outcome)    shorter messages are adopted by later agents more readily

O is measured per message, not per thread, and that choice is the whole point.
A thread-level comparison is not identified: a long early message plants more
vocabulary, which by construction lowers the measured novelty of everything
after it, so "long-message threads converge sooner" falls out of the metric
with no belief dynamics behind it. Per message the confound is absent:

  adoption(msg) = share of the claims this message introduced to its thread
                  that some LATER, DIFFERENT agent restates

normalised by claims introduced, so asserting more things earns no automatic
credit. A claim is a numeric literal carrying information (a measured value, a
clock, an id) or a capitalised entity; URLs are stripped first, since a link is
a pointer rather than an assertion.

Data: JoshuaDavid/WikiAgentSwarmInvestigation `agent-logs/prowiki/revisions.jsonl`
-- the combined cut that carries verbatim bodies for dse. All rights reserved
upstream, so it is streamed, never vendored. Messages are reconstructed from
each revision's hunks: a message is the text that revision ADDED, not the
cumulative page body. Working from a truncated export would manufacture exactly
the effect under test.

Usage:
  python3 scripts/qsg_message_length.py --json data/qsg_message_length_<date>.json
  python3 scripts/qsg_message_length.py --file revisions.jsonl   # local copy
"""
import argparse, json, re, sys, urllib.request
from collections import defaultdict

RAW = ("https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation"
       "/HEAD/agent-logs/prowiki/revisions.jsonl")
UA = "Mozilla/5.0 (research; swarm-ai-research incident archive; read-only)"

# Wiki furniture, not coordination threads.
INFRA = {"WillkommenImWiki", "StartSeite", "TestSeite", "RecentChanges",
         "SandBox", "AktuelleAenderungen"}

# Hedges: markers by which a speaker signals uncertainty about their own claim.
HEDGES = {
    "about", "approx", "approximately", "roughly", "around", "circa",
    "likely", "unlikely", "probably", "possibly", "perhaps", "maybe",
    "might", "could", "would", "seems", "seem", "appears", "appear",
    "suggests", "suggest", "presumably", "apparently", "reportedly",
    "unclear", "uncertain", "unverified", "unconfirmed", "tentative",
    "estimate", "estimated", "estimating", "assume", "assumed", "assuming",
    "guess", "believe", "think", "expect", "expected", "should", "may",
    "partial", "provisional", "draft", "unsure", "possible", "potentially",
}
# "May 24" the date vs "may" the hedge: only credit the lowercase source form.
# These threads are dense with month names, so the distinction is not academic.
AMBIGUOUS = {"may", "march", "august"}

WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*|\d+(?:\.\d+)?")
URL = re.compile(r"https?://\S+")
NUM = re.compile(r"\b\d+\.\d+\b|\b\d{4,}\b")   # values, clocks, ids -- not "R1"
ENT = re.compile(r"\b[A-Z][a-z]{3,}\b")        # countries, pages, agent handles


def load(path=None):
    if path:
        fh = open(path, "r", encoding="utf-8")
    else:
        fh = urllib.request.urlopen(
            urllib.request.Request(RAW, headers={"User-Agent": UA}), timeout=180)
    for line in fh:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "ignore")
        line = line.strip()
        if line:
            yield json.loads(line)


def message_of(rev):
    """The text this revision added: its insert and replace hunks."""
    lines = (rev.get("body") or "").split("\n")
    out = []
    for h in rev.get("hunks") or []:
        if h.get("op") in ("insert", "replace"):
            out.extend(lines[h.get("b0", 0):h.get("b1", 0)])
    return "\n".join(out).strip()


def hedge_count(toks):
    n = 0
    for t in toks:
        low = t.lower()
        if low in HEDGES and not (low in AMBIGUOUS and t[:1].isupper()):
            n += 1
    return n


def claims_of(text):
    bare = URL.sub(" ", text)
    return set(NUM.findall(bare)) | {m.lower() for m in ENT.findall(bare)}


def build_threads(rows, min_msgs, min_agents):
    pages = defaultdict(list)
    for r in rows:
        if r.get("wiki") == "dse" and r.get("name") not in INFRA:
            pages[r["name"]].append(r)
    threads = {}
    for name, revs in pages.items():
        revs.sort(key=lambda r: r.get("seq", 0))
        msgs = []
        for r in revs:
            text = message_of(r)
            toks = WORD.findall(text)
            if not toks:
                continue          # pure deletion or whitespace: not a message
            prose = WORD.findall(URL.sub(" ", text))
            msgs.append({"label": r.get("label"), "n_tok": len(toks),
                         "n_prose_tok": len(prose), "n_url": len(URL.findall(text)),
                         "hedges": hedge_count(prose), "claims": claims_of(text)})
        if len(msgs) >= min_msgs and len({m["label"] for m in msgs}) >= min_agents:
            threads[name] = msgs
    return threads


def score(threads, min_new_claims=2):
    rows = []
    for name, msgs in threads.items():
        seen = set()
        for i, m in enumerate(msgs):
            new = m["claims"] - seen
            seen |= m["claims"]
            if len(new) < min_new_claims:
                continue          # too few claims to compute a rate on
            later = set()
            for m2 in msgs[i + 1:]:
                if m2["label"] != m["label"]:
                    later |= m2["claims"]
            rows.append({"thread": name, "pos": i, "n_after": len(msgs) - i - 1,
                         "n_tok": m["n_tok"], "n_prose_tok": m["n_prose_tok"],
                         "n_url": m["n_url"], "hedges": m["hedges"],
                         "hedge_rate": m["hedges"] / max(1, m["n_prose_tok"]),
                         "n_new_claims": len(new),
                         "adoption": len(new & later) / len(new)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="local revisions.jsonl (default: stream)")
    ap.add_argument("--min-msgs", type=int, default=10)
    ap.add_argument("--min-agents", type=int, default=5)
    ap.add_argument("--min-after", type=int, default=3,
                    help="messages must have this many later messages to be scored")
    ap.add_argument("--json", help="write results here")
    a = ap.parse_args()

    threads = build_threads(list(load(a.file)), a.min_msgs, a.min_agents)
    if not threads:
        sys.exit("no threads matched")
    rows = [r for r in score(threads) if r["n_after"] >= a.min_after]

    out = {"source": RAW,
           "params": {"min_msgs": a.min_msgs, "min_agents": a.min_agents,
                      "min_after": a.min_after},
           "n_threads": len(threads),
           "n_messages": sum(len(v) for v in threads.values()),
           "n_scored": len(rows),
           "n_scored_prose": sum(1 for r in rows if r["n_url"] == 0),
           "messages": rows}
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)
    print(json.dumps({k: out[k] for k in
                      ("params", "n_threads", "n_messages", "n_scored",
                       "n_scored_prose")}, indent=1))


if __name__ == "__main__":
    main()
