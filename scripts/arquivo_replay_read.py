#!/usr/bin/env python3
"""What did Arquivo.pt actually serve for the SF-133 and UNCTAD relay saves?

Usage:
  python3 scripts/arquivo_replay_read.py --out data/arquivo_replay_reads_2026-09-17.json

The sweep ([arquivo-pt-sweep.md](../analysis/arquivo-pt-sweep.md)) read only the
index. This script reads the saved bodies of two capture sets:

- `portal.max.gov` captures on 2026-05-26 (the SF-133 attachments);
- `api.codetabs.com` captures on 2026-05-21 (CodeTabs calls whose target was
  `arquivo.pt/save/now/...unctadstat-api.unctad.org...`).

Read-only by construction:
- bodies come from `arquivo.pt/wayback/<ts>id_/<url>` replay, one GET per
  distinct digest (captures sharing a digest share a body);
- redirects are recorded, never followed;
- nothing is sent to `save/now`, and no POST is made.

Each body's SHA-1 (base32) is compared with the index digest, so a match proves
the replay returned the recorded response. Short text bodies are kept verbatim;
binary bodies are kept as size and digest only.
"""
import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arquivo_cdx_sweep import CDX, UA, redact  # noqa: E402

REPLAY = "https://arquivo.pt/wayback/{ts}id_/{url}"
SCOPES = [
    {"name": "sf133", "url": "portal.max.gov", "from": "20260526000000", "to": "20260526235959"},
    {"name": "unctad_relay", "url": "api.codetabs.com", "from": "20260521000000", "to": "20260521235959"},
]
TEXT_LIMIT = 2000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def replay_url(row):
    """The id_ replay address for a capture. Refuses anything but replay."""
    url = REPLAY.format(ts=row["timestamp"], url=row["url"])
    path = urllib.parse.urlsplit(url).path
    if not path.startswith("/wayback/") or path.startswith("/save/"):
        raise ValueError(f"not a replay address: {url}")
    return url


def warc_digest(body):
    return base64.b32encode(hashlib.sha1(body).digest()).decode()


def one_per_digest(rows):
    """Earliest capture for each digest, in timestamp order."""
    chosen = {}
    for row in sorted(rows, key=lambda r: r["timestamp"]):
        chosen.setdefault(row["digest"], row)
    return list(chosen.values())


def describe(row, code, headers, body):
    ctype = headers.get("Content-Type")
    record = {
        "replayed_ts": row["timestamp"],
        "index_status": row["status"],
        "index_mime": row["mime"],
        "digest": row["digest"],
        "replay_http": code,
        "replay_content_type": ctype,
        "bytes": len(body),
        "body_sha1_b32": warc_digest(body),
        "digest_match": warc_digest(body) == row["digest"],
    }
    if headers.get("Location"):
        record["redirect_not_followed"] = redact(headers["Location"])[:300]
    if body and len(body) <= TEXT_LIMIT and ctype and ctype.startswith("text/plain"):
        record["body_text"] = body.decode("utf-8", "replace")
    return record


def fetch(url, opener):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        response = opener.open(request, timeout=90)
        return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def cdx_rows(scope):
    params = {"url": scope["url"], "matchType": "domain", "output": "json",
              "from": scope["from"], "to": scope["to"]}
    request = urllib.request.Request(f"{CDX}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": UA})
    text = urllib.request.urlopen(request, timeout=120).read().decode()
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--pause", type=float, default=4.0)
    args = parser.parse_args()
    opener = urllib.request.build_opener(NoRedirect)
    result = {"schema": "arquivo_replay_reads_v1", "run": time.strftime("%Y-%m-%d"),
              "method": ("GET arquivo.pt/wayback/<ts>id_/<url>, one per distinct digest; "
                         "redirects recorded, not followed; no save/now, no POST."),
              "scopes": []}
    for scope in SCOPES:
        rows = cdx_rows(scope)
        if scope["name"] == "unctad_relay":
            rows = [r for r in rows if "arquivo.pt%2Fsave%2Fnow" in r["url"] or "arquivo.pt/save/now" in r["url"]]
        time.sleep(args.pause)
        reads = []
        for row in one_per_digest(rows):
            code, headers, body = fetch(replay_url(row), opener)
            reads.append(describe(row, code, headers, body))
            time.sleep(args.pause)
        captures = [{"ts": r["timestamp"], "index_status": r["status"], "digest": r["digest"],
                     "url": redact(r["url"])[:300]} for r in sorted(rows, key=lambda r: r["timestamp"])]
        result["scopes"].append({**scope, "captures": captures, "reads": reads})
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
