#!/usr/bin/env python3
"""Classify swarm revision bodies by LLM family via the she-llac classifier.

API: https://glove.she-llac.com/llm-family/llm.txt (Pangram 3.3.2 + family
probe; labels Claude / ChatGPT / Gemini / Other plus an ai_probability). The
doc's own caveat applies: check ai_probability before reading the family label,
and treat neither as proof of authorship. Submitted text is logged by the
operator; only public wiki text goes through here.

Three steps:

  sample     draw stratified bodies from the public export into a JSONL of
             {id, group, text}. Groups are <wiki>:<ISO week>; --per-group caps
             each. Bodies are deduplicated by hash and trimmed to --max-chars.
             Extra hand-picked texts can be appended as {"id","group","text"}.
  submit     send the sample in batches of 256 with Prefer: respond-async and
             a saved Idempotency-Key per batch, then poll each job. Needs
             LLM_FAMILY_KEY in the environment. Resumable: re-running reuses
             the saved keys and job URLs, so nothing is charged twice.
  summarize  per-group family distribution and mean ai_probability.

  python3 scripts/llm_family.py sample --per-group 40 --out /tmp/llm_sample.jsonl
  LLM_FAMILY_KEY=... python3 scripts/llm_family.py submit /tmp/llm_sample.jsonl --state /tmp/llm_jobs.json
  python3 scripts/llm_family.py summarize /tmp/llm_jobs.json
"""
import argparse, collections, hashlib, json, os, random, sys, time, urllib.request, uuid
from datetime import datetime
from pathlib import Path

BASE = "https://glove.she-llac.com/llm-family"
RAW = "https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation/HEAD/agent-logs/"
UA = "swarm-ai-research incident archive (llm_family.py)"


def iter_export(name):
    req = urllib.request.Request(RAW + f"{name}/revisions.jsonl", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass


def cmd_sample(a):
    random.seed(a.seed)
    buckets = collections.defaultdict(list)
    seen = set()
    for rev in iter_export("prowiki"):
        body = (rev.get("body") or "").strip()
        if len(body) < a.min_chars:
            continue
        h = hashlib.sha1(body.encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        try:
            wk = datetime.fromisoformat(rev["time"].replace("Z", "+00:00")).strftime("%G-W%V")
        except Exception:  # noqa: BLE001
            wk = "unknown"
        buckets[f"{rev.get('wiki')}:{wk}"].append({"id": rev["rev_id"], "group": f"{rev.get('wiki')}:{wk}",
                                                  "label": rev.get("label"), "page": rev.get("name"),
                                                  "text": body[: a.max_chars]})
    n = 0
    with open(a.out, "w") as f:
        for g in sorted(buckets):
            rows = buckets[g]
            random.shuffle(rows)
            for r in rows[: a.per_group]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n += 1
            print(f"{g:22s} {len(rows):6d} available -> {min(len(rows), a.per_group)} sampled", file=sys.stderr)
    print(f"{n} texts -> {a.out}", file=sys.stderr)


def api(method, url, key, body=None, headers=None):
    h = {"Authorization": f"Bearer {key}", "User-Agent": UA, "Prefer": "respond-async"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, dict(e.headers), payload


def cmd_submit(a):
    key = os.environ.get("LLM_FAMILY_KEY")
    if not key:
        sys.exit("set LLM_FAMILY_KEY in the environment (never on the command line or in files)")
    rows = [json.loads(l) for l in open(a.input) if l.strip()]
    state = json.loads(Path(a.state).read_text()) if Path(a.state).exists() else {"input": a.input, "batches": []}
    batches = [rows[i:i + 256] for i in range(0, len(rows), 256)]
    while len(state["batches"]) < len(batches):
        state["batches"].append({"idem": str(uuid.uuid4()), "job_url": None, "results": None})
    Path(a.state).write_text(json.dumps(state, indent=1))
    for bi, (batch, st) in enumerate(zip(batches, state["batches"])):
        if st["results"]:
            continue
        if not st["job_url"]:
            code, hdr, payload = api("POST", f"{BASE}/v1/predict", key, {"text": [r["text"] for r in batch]},
                                     {"Idempotency-Key": st["idem"]})
            if code in (200,) and payload.get("results"):
                st["results"] = payload["results"]
            elif code in (202, 303, 200):
                st["job_url"] = payload.get("url") or hdr.get("Location")
            elif code == 429:
                print(f"batch {bi}: 429 {payload}; retry after {hdr.get('Retry-After')}s", file=sys.stderr)
                time.sleep(int(hdr.get("Retry-After", "30")))
                continue
            else:
                sys.exit(f"batch {bi}: HTTP {code} {payload}")
            Path(a.state).write_text(json.dumps(state, indent=1))
            print(f"batch {bi}: submitted ({len(batch)} texts) -> {st['job_url'] or 'done'}", file=sys.stderr)
    # poll
    pending = True
    while pending:
        pending = False
        for bi, st in enumerate(state["batches"]):
            if st["results"] or not st["job_url"]:
                continue
            code, hdr, payload = api("GET", st["job_url"], key)
            if code == 200 and payload.get("results"):
                st["results"] = payload["results"]
                Path(a.state).write_text(json.dumps(state, indent=1))
                print(f"batch {bi}: complete", file=sys.stderr)
            elif code == 202:
                pending = True
            elif code == 429:
                pending = True
                time.sleep(int(hdr.get("Retry-After", "10")))
            else:
                print(f"batch {bi}: HTTP {code} {payload}", file=sys.stderr)
        if pending:
            time.sleep(6)
    print("all batches complete", file=sys.stderr)


def cmd_summarize(a):
    state = json.loads(Path(a.state).read_text())
    rows = [json.loads(l) for l in open(state["input"]) if l.strip()]
    results = [r for b in state["batches"] for r in (b["results"] or [])]
    if len(results) < len(rows):
        print(f"{len(results)}/{len(rows)} classified so far", file=sys.stderr)
    by = collections.defaultdict(list)
    for row, res in zip(rows, results):
        by[row["group"]].append(res)
    print(f"{'group':22s} {'n':>4} {'ai_p':>5}  Claude ChatGPT Gemini  Other")
    for g in sorted(by):
        rs = by[g]
        fam = collections.Counter(r["predicted_family"] for r in rs)
        ai = sum(r["ai_probability"] for r in rs) / len(rs)
        print(f"{g:22s} {len(rs):4d} {ai:5.2f}  " + " ".join(f"{fam.get(k,0)/len(rs):6.0%}" for k in ("Claude", "ChatGPT", "Gemini", "Other")))
    if a.json:
        Path(a.json).write_text(json.dumps([{**row, "text": None, **res} for row, res in zip(rows, results)], indent=0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--per-group", type=int, default=40); s.add_argument("--min-chars", type=int, default=300)
    s.add_argument("--max-chars", type=int, default=4000); s.add_argument("--seed", type=int, default=1); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_sample)
    p = sub.add_parser("submit"); p.add_argument("input"); p.add_argument("--state", required=True); p.set_defaults(fn=cmd_submit)
    z = sub.add_parser("summarize"); z.add_argument("state"); z.add_argument("--json", help="write per-text results (without bodies)"); z.set_defaults(fn=cmd_summarize)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
