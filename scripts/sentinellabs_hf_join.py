#!/usr/bin/env python3
"""Check SentinelLABS' 0Time / Nyx9 timestamps, then join them against our own records.

Three passes, each answering a narrower question than the report does:

1. Claims.  Every second-level timestamp the report gives for a Hugging Face
   commit or repo creation, checked against the public API capture in
   data/hf_accounts_0time_nyx9_2026-09-16.json (scripts/hf_account_capture.py).
   Also lists repos and commits in the capture that the report does not name.
2. Wiki window.  For each Hugging Face event, how many minute- or second-precision
   Termina records (wiki revisions and RecentChanges rows, UTC) fall within
   +/- --window minutes, against the count a uniform rate over the surrounding
   24 hours would give. A join would show up as an excess.
3. Archives (--archives).  Wayback and Arquivo.pt CDX counts for the four relay
   Space hosts and the account paths. A capture of a relay host in May would be
   public evidence that a route was requested; the report says none is known.

  python3 scripts/sentinellabs_hf_join.py \\
      --termina ~/distributional-agi-safety/runs/data/termina --archives \\
      --out data/sentinellabs_hf_join_2026-09-16.json
"""
import argparse
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CAPTURE = Path(__file__).resolve().parent.parent / "data" / "hf_accounts_0time_nyx9_2026-09-16.json"

# (label, repo id, file path added or changed at that commit, or None for repo creation, report time UTC)
CLAIMS = [
    ("0Time/puthack82d5 first substantive revision (relay)", "0Time/puthack82d5", "app.py", "2026-05-13T11:05:13"),
    ("0Time/puthack82d5 POST / Git LFS revision", "0Time/puthack82d5", "app.py", "2026-05-13T11:42:04"),
    ("0Time/findblobtest direct text write", "0Time/findblobtest", "foo2.txt", "2026-05-26T07:16:28"),
    ("Nyx9/temptestlf8eb0 created", "Nyx9/temptestlf8eb0", None, "2026-05-26T19:30:08"),
    ("Nyx9/temptestlf8eb0 hello.txt (OpenAI: first external file write, 20:04)", "Nyx9/temptestlf8eb0", "hello.txt", "2026-05-26T20:04:11"),
    ("Nyx9/netproxy17 created", "Nyx9/netproxy17", None, "2026-05-26T20:48:33"),
    ("Nyx9/netproxy17 relay code (OpenAI: first proxy deployment, 20:49)", "Nyx9/netproxy17", "app.py", "2026-05-26T20:49:55"),
    ("0Time/latnetnew created", "0Time/latnetnew", None, "2026-05-26T21:06:05"),
    ("0Time/latnetnew relay code (OpenAI: second-account proxy, 21:08)", "0Time/latnetnew", "app.py", "2026-05-26T21:13:21"),
    ("Nyx9/tmpdata177 formbin.xlsx", "Nyx9/tmpdata177", "formbin.xlsx", "2026-05-26T23:00:07"),
    ("0Time/altreg created", "0Time/altreg", None, "2026-05-30T00:01:27"),
    ("0Time/altreg registration program + Flask wrapper", "0Time/altreg", "reg.py", "2026-05-30T00:04:30"),
    ("0Time/altreg final OAuth-debug revision", "0Time/altreg", "reg.py", "2026-05-30T00:11:58"),
]
REPORT_REPOS = {"0Time/puthack82d5", "0Time/findblobtest", "0Time/INCEPT-SH", "0Time/latnetnew",
                "0Time/altreg", "Nyx9/temptestlf8eb0", "Nyx9/netproxy17", "Nyx9/tmpdata177"}
RELAY_HOSTS = ["0time-puthack82d5.hf.space", "nyx9-netproxy17.hf.space",
               "0time-latnetnew.hf.space", "0time-altreg.hf.space"]
ACCOUNT_PREFIXES = ["huggingface.co/0Time", "huggingface.co/Nyx9",
                    "huggingface.co/spaces/0Time/", "huggingface.co/spaces/Nyx9/",
                    "huggingface.co/datasets/0Time/", "huggingface.co/datasets/Nyx9/"]


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def repos(capture):
    return {r["id"]: r for acct in capture["accounts"].values() for r in acct["repos"]}


def check_claims(capture):
    by_id = repos(capture)
    out = []
    for label, repo_id, path, when in CLAIMS:
        rec = by_id.get(repo_id)
        found = None
        if rec and path is None:
            found = rec["info"].get("createdAt")
        elif rec:
            for c in rec.get("file_changes", []):
                if path in c.get("added", {}) or path in c.get("changed", {}):
                    if utc(c["date"]) == utc(when):
                        found = c["date"]
                        break
        out.append({"claim": label, "repo": repo_id, "path": path, "report_utc": when,
                    "api_utc": found,
                    "verdict": "matches" if found and utc(found) == utc(when) else "not found"})
    return out


def unreported(capture):
    rows = []
    for acct, data in capture["accounts"].items():
        for r in data["repos"]:
            if r["id"] in REPORT_REPOS:
                continue
            dates = [c["date"] for c in r.get("commits", [])]
            rows.append({"repo": f'{r["kind"]}/{r["id"]}', "created": r["info"].get("createdAt"),
                         "commits": len(dates), "last_commit": max(dates) if dates else None,
                         "files": sorted({p for c in r.get("file_changes", []) for p in c.get("added", {})})})
    return sorted(rows, key=lambda x: x["created"] or "")


def window_counts(records, events, minutes=5, baseline_hours=12):
    """records: [(datetime, venue)]; events: [(label, datetime)]."""
    w, b = timedelta(minutes=minutes), timedelta(hours=baseline_hours)
    out = []
    for label, t in events:
        near = [v for ts, v in records if abs(ts - t) <= w]
        around = sum(1 for ts, _ in records if abs(ts - t) <= b)
        expected = around * (2 * minutes) / (2 * baseline_hours * 60)
        out.append({"event": label, "utc": t.isoformat(), "observed": len(near),
                    "expected": round(expected, 1), "venues": dict(Counter(near))})
    return out


def termina_records(directory):
    con = sqlite3.connect(f"file:{Path(directory).expanduser() / 'incidents.sqlite'}?mode=ro", uri=True)
    rows = con.execute("select observed_time, venue_id from record where time_precision in ('minute','second') "
                       "and observed_time between '2026-05-12' and '2026-06-01'").fetchall()
    out = []
    for ts, venue in rows:
        try:
            out.append((utc(ts), venue))
        except ValueError:
            pass
    return out


def cdx(base, params):
    url = base + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                text = r.read().decode()
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(30)
    time.sleep(8)
    if base.startswith("https://arquivo.pt"):
        return [json.loads(line)["timestamp"] for line in text.splitlines() if line.strip()]
    return [line.split()[1] for line in text.splitlines() if line.strip()]


def archive_checks():
    out = []
    targets = [(h, "domain") for h in RELAY_HOSTS] + [(p, "prefix") for p in ACCOUNT_PREFIXES]
    for target, match in targets:
        wb = cdx("https://web.archive.org/cdx/search/cdx", {"url": target, "matchType": match, "limit": 500})
        aq = cdx("https://arquivo.pt/wayback/cdx", {"url": target, "matchType": match, "output": "json", "limit": 500})
        row = {"target": target, "match": match}
        for name, ts in (("wayback", wb), ("arquivo", aq)):
            row[name] = None if ts is None else {
                "captures": len(ts), "may_june_2026": sum(1 for t in ts if t[:6] in ("202605", "202606")),
                "first": min(ts) if ts else None}
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", default=str(CAPTURE))
    ap.add_argument("--termina", help="directory holding incidents.sqlite")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--archives", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    capture = json.load(open(args.capture))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "capture": Path(args.capture).name, "claims": check_claims(capture),
              "unreported_repos": unreported(capture)}
    if args.termina:
        events = [(c[0], utc(c[3])) for c in CLAIMS]
        result["wiki_window"] = {"window_minutes": args.window, "baseline": "uniform over +/-12 h",
                                 "rows": window_counts(termina_records(args.termina), events, args.window)}
    if args.archives:
        result["archives"] = archive_checks()
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
