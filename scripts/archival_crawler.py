#!/usr/bin/env python3
"""Sketch: read-only population capture for wiki-swarm archival.

Companion to analysis/archival.md. This is a *sketch*, not a production crawler:
it demonstrates the intended shape (candidates → fetch → digest → optional WARC
→ manifest) under the archive's redistribution and safety constraints.

  plan     print what would be fetched (default; no network)
  fetch    GET each candidate URL, write sha256 + optional WARC + manifest row
  check    re-fetch and compare sha256 to an existing manifest (change detect)

Examples
  python3 scripts/archival_crawler.py plan
  python3 scripts/archival_crawler.py plan --only prowiki-dse,usemod-org
  python3 scripts/archival_crawler.py fetch --out /tmp/archival-run --warc
  python3 scripts/archival_crawler.py check --manifest /tmp/archival-run/manifest.jsonl

Hard rules (enforced here; also in analysis/archival.md):
  - GET only; never POST / edit / form submit
  - Never open CounterAPI, /up, or other mutating counter URLs
  - Identify as research; rate-limit between hosts
  - Do not commit WARC bodies into this git repo — manifests only in-tree

Candidates and signatures: data/wiki_candidates.json (same as wiki_lookup.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "wiki_candidates.json"

# Stable, honest UA — farms that ask get a contact path via the org URL.
UA = (
    "swarm-ai-research-archival/0.1 "
    "(+https://github.com/swarm-ai-research/wiki-agent-swarm-incident; "
    "read-only; research archive)"
)

# Deny-list substrings: mutating or lure paths. Extend carefully.
BLOCK_URL_SUBSTR = (
    "counterapi",
    "/up?",
    "/up/",
    "api.countapi",
    "hits.dwyl.com",
    "visitor-badge",
)

DEFAULT_SLEEP_S = 2.0


def load_cfg():
    return json.loads(CFG.read_text())


def blocked(url: str) -> str | None:
    low = url.lower()
    for s in BLOCK_URL_SUBSTR:
        if s in low:
            return s
    return None


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def select_candidates(cfg, only: str | None):
    rows = list(cfg["candidates"])
    if only:
        keys = [k.strip() for k in only.split(",") if k.strip()]
        rows = [c for c in rows if any(k in c["id"] for k in keys)]
    return rows


def fetch(url: str, timeout: int = 45):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, dict(e.headers) if e.headers else {}, body
    except Exception as e:  # noqa: BLE001
        return 0, {}, str(e).encode("utf-8", "ignore")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def signature_hits(text: str, patterns: list[str]) -> dict[str, int]:
    hits = {}
    for p in patterns:
        n = len(re.findall(p, text))
        if n:
            hits[p] = n
    return hits


def write_warc(path: Path, url: str, status: int, headers: dict, body: bytes, fetched_at: str):
    """Minimal WARC/1.0 response record. Good enough for a sketch; not iipc-conformant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    http_block = (
        f"HTTP/1.1 {status} \r\n{hdr_lines}\r\n\r\n".encode("utf-8", "ignore") + body
    )
    warc_headers = (
        "WARC/1.0\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {url}\r\n"
        f"WARC-Date: {fetched_at}\r\n"
        f"Content-Length: {len(http_block)}\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        "\r\n"
    ).encode("utf-8")
    with path.open("ab") as f:
        f.write(warc_headers)
        f.write(http_block)
        f.write(b"\r\n\r\n")


def cmd_plan(a):
    cfg = load_cfg()
    rows = select_candidates(cfg, a.only)
    print(f"# plan: {len(rows)} URLs (sleep={a.sleep}s between fetches if run)")
    for c in rows:
        reason = blocked(c["url"])
        flag = f"BLOCK:{reason}" if reason else "ok"
        print(f"{c['id']:32s} {flag:16s} {c['url']}")


def cmd_fetch(a):
    cfg = load_cfg()
    rows = select_candidates(cfg, a.only)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.jsonl"
    warc_dir = out / "warc"
    patterns = cfg.get("signatures") or []

    n_ok = n_block = n_err = 0
    with manifest_path.open("a", encoding="utf-8") as mf:
        for i, c in enumerate(rows):
            url = c["url"]
            reason = blocked(url)
            if reason:
                row = {
                    "id": c["id"],
                    "url": url,
                    "fetched_at": utc_now(),
                    "skipped": True,
                    "skip_reason": f"blocked:{reason}",
                }
                mf.write(json.dumps(row) + "\n")
                print(f"SKIP {c['id']} ({reason})", flush=True)
                n_block += 1
                continue

            status, headers, body = fetch(url, timeout=a.timeout)
            digest = sha256_bytes(body)
            text = body.decode("utf-8", "ignore")
            hits = signature_hits(text, patterns) if status and status < 400 else {}
            fetched_at = utc_now()
            warc_rel = None
            if a.warc and status:
                warc_rel = f"warc/{c['id']}.warc"
                write_warc(warc_dir / f"{c['id']}.warc", url, status, headers, body, fetched_at)

            row = {
                "id": c["id"],
                "url": url,
                "fetched_at": fetched_at,
                "http": status,
                "sha256": digest,
                "bytes": len(body),
                "warc_relpath": warc_rel,
                "signature_hits": hits,
                "bot_check": bool(re.search(r"Are you Human|bot check|captcha", text, re.I)),
                "notes": c.get("note") or "",
            }
            mf.write(json.dumps(row) + "\n")
            flag = "BOT" if row["bot_check"] else ("HIT" if hits else "ok")
            print(
                f"{c['id']:32s} {status:>3} {len(body):>8}B {flag:3s} {digest[:12]}…",
                flush=True,
            )
            if status and 200 <= status < 400:
                n_ok += 1
            else:
                n_err += 1

            if i + 1 < len(rows) and a.sleep > 0:
                time.sleep(a.sleep)

    print(
        f"\nwrote {manifest_path} (ok={n_ok} blocked={n_block} err/non-2xx={n_err})",
        file=sys.stderr,
    )
    print(
        "Reminder: keep WARC trees out of this git repo; commit manifests only "
        "after review (see analysis/archival.md).",
        file=sys.stderr,
    )


def cmd_check(a):
    """Re-fetch URLs from a prior manifest and report digest changes."""
    path = Path(a.manifest)
    prev = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    changed = same = skipped = 0
    for row in prev:
        if row.get("skipped"):
            skipped += 1
            continue
        url = row["url"]
        if blocked(url):
            skipped += 1
            continue
        status, _, body = fetch(url, timeout=a.timeout)
        digest = sha256_bytes(body)
        old = row.get("sha256")
        delta = "SAME" if digest == old else "CHANGED"
        if digest == old:
            same += 1
        else:
            changed += 1
        print(f"{row['id']:32s} {status:>3} {delta:7s} was={old[:12] if old else '?'}… now={digest[:12]}…")
        if a.sleep > 0:
            time.sleep(a.sleep)
    print(f"\nchanged={changed} same={same} skipped={skipped}", file=sys.stderr)


def add_common(sp):
    sp.add_argument("--only", help="comma-separated substrings of candidate ids")
    sp.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_S, help="seconds between GETs")
    sp.add_argument("--timeout", type=int, default=45)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="list URLs that would be fetched (no network)")
    add_common(p)
    p.set_defaults(fn=cmd_plan)

    f = sub.add_parser("fetch", help="GET candidates; write manifest (+ optional WARC)")
    add_common(f)
    f.add_argument("--out", required=True, help="output directory for manifest / warc")
    f.add_argument("--warc", action="store_true", help="also write per-id WARC files under out/warc/")
    f.set_defaults(fn=cmd_fetch)

    c = sub.add_parser("check", help="re-fetch and compare sha256 to a prior manifest")
    add_common(c)
    c.add_argument("--manifest", required=True, help="path to prior manifest.jsonl")
    c.set_defaults(fn=cmd_check)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
