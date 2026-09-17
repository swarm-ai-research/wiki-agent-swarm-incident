#!/usr/bin/env python3
"""Metadata capture of the Hugging Face accounts 0Time and Nyx9.

SentinelLABS ("Agents at Large", Tom Hegel, 2026-09-16) names these two accounts
as likely containers for OpenAI's May 2026 WebCache activity on Hugging Face.
Spaces there are already being paused (0Time/altreg is flagged abusive), so this
records what the public API still shows before more of it goes.

Read-only, public API only, about one request a second. Per account: the profile
overview and every public model, dataset and Space. Per repo: its info record
(creation time, head sha, Space runtime stage), the full commit list, and the
file tree at every commit, reduced to what each commit added, removed or changed
(path, size, blob oid, LFS sha256). No file body is fetched or stored, so no
token, relay code or registration script is re-hosted. Also records 0Time's
discussions on Anthropic/BioMysteryBench-full, which the report cites.

  python3 scripts/hf_account_capture.py --out data/hf_accounts_0time_nyx9_2026-09-16.json
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://huggingface.co/api"
ACCOUNTS = ("0Time", "Nyx9")
KINDS = ("models", "datasets", "spaces")
BIOMYSTERY = "datasets/Anthropic/BioMysteryBench-full"
PAUSE = 1.0
MAX_TREE_COMMITS = 80


def get(path, params=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        time.sleep(PAUSE)
        req = urllib.request.Request(url, headers={"User-Agent": "wiki-agent-swarm-incident research capture"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):
                return {"_http_error": e.code}
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(15)


def commits(kind, repo_id):
    out, page = [], 0
    while True:
        batch = get(f"{kind}/{repo_id}/commits/main", {"p": page})
        if not isinstance(batch, list) or not batch:
            return out if out or isinstance(batch, list) else batch
        out += [{"id": c["id"], "date": c["date"], "title": c.get("title", ""),
                 "authors": [a.get("user") for a in c.get("authors", [])]} for c in batch]
        if len(batch) < 50:
            return out
        page += 1


def tree(kind, repo_id, rev):
    rows = get(f"{kind}/{repo_id}/tree/{rev}", {"recursive": "true"})
    if not isinstance(rows, list):
        return None
    return {r["path"]: {"size": r.get("size"), "oid": r.get("oid"),
                        "lfs_sha256": (r.get("lfs") or {}).get("oid")}
            for r in rows if r.get("type") == "file"}


def file_changes(kind, repo_id, commit_list):
    """Diff consecutive trees, oldest commit first."""
    prev, changes = {}, []
    for c in reversed(commit_list[:MAX_TREE_COMMITS]):
        cur = tree(kind, repo_id, c["id"])
        if cur is None:
            changes.append({"commit": c["id"], "error": "tree unavailable"})
            continue
        changes.append({
            "commit": c["id"], "date": c["date"],
            "added": {p: v for p, v in cur.items() if p not in prev},
            "removed": sorted(p for p in prev if p not in cur),
            "changed": {p: v for p, v in cur.items() if p in prev and prev[p]["oid"] != v["oid"]},
        })
        prev = cur
    return changes


def repo_record(kind, repo_id):
    info = get(f"{kind}/{repo_id}")
    keep = ("createdAt", "lastModified", "sha", "private", "gated", "disabled", "sdk",
            "cardData", "tags", "downloads", "likes", "_http_error")
    rec = {"kind": kind, "id": repo_id, "info": {k: info[k] for k in keep if k in info}}
    if kind == "spaces" and "runtime" in info:
        rt = info["runtime"]
        rec["info"]["runtime"] = {k: rt.get(k) for k in ("stage", "errorMessage", "hardware")}
    rec["commits"] = commits(kind, repo_id)
    if isinstance(rec["commits"], list):
        rec["file_changes"] = file_changes(kind, repo_id, rec["commits"])
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source": "https://huggingface.co/api (public, unauthenticated)",
              "report": "https://www.sentinelone.com/labs/agents-at-large-tracing-illicit-openai-agent-activity-on-hugging-face/",
              "note": "Handles are evidence containers, not actor names. Metadata only; no file bodies.",
              "accounts": {}}
    for user in ACCOUNTS:
        overview = get(f"users/{user}/overview")
        acct = {"overview": {k: overview.get(k) for k in
                             ("createdAt", "numModels", "numDatasets", "numSpaces",
                              "numDiscussions", "isPro", "_http_error") if k in overview},
                "repos": []}
        for kind in KINDS:
            listing = get(kind, {"author": user, "limit": 1000})
            for r in listing if isinstance(listing, list) else []:
                print(f"{kind}/{r['id']}", file=sys.stderr)
                acct["repos"].append(repo_record(kind, r["id"]))
        result["accounts"][user] = acct

    disc = get(f"{BIOMYSTERY}/discussions", {"author": "0Time"})
    result["biomysterybench_discussions_by_0Time"] = [
        {k: d.get(k) for k in ("num", "title", "createdAt", "isPullRequest", "status")}
        for d in (disc.get("discussions", []) if isinstance(disc, dict) else [])
        if (d.get("author") or {}).get("name") == "0Time"]

    with open(args.out, "w") as f:
        json.dump(result, f, indent=1, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    main()
