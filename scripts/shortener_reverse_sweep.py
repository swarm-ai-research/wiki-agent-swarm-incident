#!/usr/bin/env python3
"""Safely sweep archives and explicit public listings for unresolved short URLs.

The script never requests a bare short URL and never follows redirects. Archive
CDX records are queried for indexed redirect targets; live requests are limited
to listing/statistics URLs explicitly recorded in the Termina venue registry.
Response bodies are not persisted.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "shortener_reverse_index_2026-09-09.json"
DB = ROOT / "data" / "termina" / "incidents.sqlite"
OUTPUT = ROOT / "data" / "shortener_reverse_sweep_2026-09-09.json"
CDX = "https://web.archive.org/cdx/search/cdx"
UA = "wiki-agent-swarm-incident-readonly-research/1.0"
SENSITIVE_QUERY_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|passwd|auth|credential)", re.I)
COUNTER_HOST = re.compile(r"(?:counterapi|countapi)", re.I)
SHORT_CODE_PATH = re.compile(r"^/[A-Za-z0-9_-]+/?$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Containers(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, list[str]]] = []
        self.containers: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"tr", "li"}:
            self.stack.append((tag, []))
        for _tag, parts in self.stack:
            for key, value in attrs:
                if key in {"href", "data-url"} and value:
                    parts.append(value)

    def handle_data(self, data):
        for _tag, parts in self.stack:
            parts.append(data)

    def handle_endtag(self, tag):
        for pos in range(len(self.stack) - 1, -1, -1):
            if self.stack[pos][0] == tag:
                _tag, parts = self.stack.pop(pos)
                self.containers.append(" ".join(parts))
                break


def fetch(url: str, timeout: int = 45) -> tuple[int, str, str | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/html;q=0.8"})
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace"), response.headers.get("Location"), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return exc.code, body, exc.headers.get("Location"), None
    except Exception as exc:  # transport errors are evidence, not fatal to the sweep
        return 0, "", None, f"{type(exc).__name__}: {exc}"


def redact_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if not parsed.query:
        return value
    query = []
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if SENSITIVE_QUERY_KEY.search(key) else item))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def safe_listing_url(url: str, short_hosts: set[str]) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path or "/"
    if parsed.scheme not in {"http", "https"} or host not in short_hosts or COUNTER_HOST.search(host):
        return False
    if SHORT_CODE_PATH.fullmatch(path) and not path.endswith("+"):
        return False
    return (
        path in {"", "/"}
        or "admin/index.php" in path.casefold()
        or path.endswith("+")
        or "public" in path.casefold()
    )


def cdx_url(seed: str) -> str:
    query = {
        "url": seed,
        "matchType": "exact",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest,redirect",
        "limit": "1000",
    }
    return f"{CDX}?{urllib.parse.urlencode(query)}"


def parse_cdx(body: str) -> list[dict]:
    payload = json.loads(body) if body.strip() else []
    if not payload:
        return []
    header = payload[0]
    return [dict(zip(header, row)) for row in payload[1:]]


def query_code(code: str, timeout: int) -> dict:
    status, body, location, error = fetch(cdx_url(code), timeout)
    rows = []
    parse_error = None
    if status == 200:
        try:
            rows = parse_cdx(body)
        except (json.JSONDecodeError, TypeError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
    captures = []
    targets = set()
    for row in rows:
        redirect = row.get("redirect")
        if redirect and redirect not in {"-", code, f"http://{code}", f"https://{code}"}:
            redirect = redact_url(redirect)
            targets.add(redirect)
        captures.append({key: row.get(key) for key in ("timestamp", "original", "statuscode", "mimetype", "digest", "redirect")})
        if captures[-1]["redirect"]:
            captures[-1]["redirect"] = redact_url(captures[-1]["redirect"])
    return {
        "short_code": code,
        "query": cdx_url(code),
        "http": status,
        "location": location,
        "error": error or parse_error,
        "capture_count": len(captures),
        "redirect_targets": sorted(targets),
        "captures": captures,
    }


def normalized(text: str) -> str:
    value = html.unescape(text)
    for _ in range(3):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            break
        value = decoded
    return value.casefold()


def scan_listing(body: str, unresolved: list[str], destination_needles: dict[str, list[str]]) -> dict:
    parser = Containers()
    parser.feed(body)
    whole = normalized(body)
    code_hits = sorted(code for code in unresolved if code.casefold() in whole)
    destination_hits = sorted(
        target for target, needles in destination_needles.items()
        if any(needle in whole for needle in needles)
    )
    pairs = set()
    for container in parser.containers:
        text = normalized(container)
        codes = [code for code in code_hits if code.casefold() in text]
        targets = [
            target for target in destination_hits
            if any(needle in text for needle in destination_needles[target])
        ]
        for code in codes:
            for target in targets:
                pairs.add((code, target))
    return {
        "short_code_hits": code_hits,
        "destination_hits": destination_hits,
        "row_pairs": [{"short_code": code, "target_id": target} for code, target in sorted(pairs)],
    }


def destination_needles(index: dict) -> dict[str, list[str]]:
    result = {}
    for row in index["destinations"]:
        candidates = {row["label"], row["target_id"].split(":", 1)[-1]}
        needles = sorted({normalized(value) for value in candidates if len(value) >= 12 and ("/" in value or "." in value)})
        if needles:
            result[row["target_id"]] = needles
    return result


def load_listings(db_path: Path) -> tuple[set[str], list[dict]]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT id, host, listing_urls FROM venue WHERE kind = 'shortener' ORDER BY id").fetchall()
    finally:
        connection.close()
    hosts = {host.casefold() for _id, host, _urls in rows if host and host != "unknown"}
    listings = []
    for venue_id, host, raw_urls in rows:
        for url in json.loads(raw_urls):
            listings.append({"venue_id": venue_id, "host": host, "url": url})
            listed_host = urllib.parse.urlsplit(url).hostname
            if listed_host:
                hosts.add(listed_host.casefold())
    return hosts, listings


def _parallel(items, function, workers: int, delay: float):
    """Run bounded read-only requests concurrently and preserve input order."""
    def invoke(item):
        result = function(item)
        if delay:
            time.sleep(delay)
        return result

    if workers <= 1:
        return [invoke(item) for item in items]
    results = [None] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(invoke, item): pos for pos, item in enumerate(items)}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results


def run(
    index_path: Path, db_path: Path, *, do_cdx: bool, do_listings: bool,
    timeout: int, delay: float, workers: int = 1, previous: dict | None = None,
) -> dict:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    unresolved = index["unresolved_codes"]
    hosts, listings = load_listings(db_path)
    hosts.update(code.split("/", 1)[0].casefold() for code in unresolved)
    archive_results = []
    archive_queries_this_run = 0
    if do_cdx:
        prior_archive = {row["short_code"]: row for row in (previous or {}).get("archive_results", [])}
        todo = [code for code in unresolved if prior_archive.get(code, {}).get("http") != 200]
        archive_queries_this_run = len(todo)
        fresh = {row["short_code"]: row for row in _parallel(todo, lambda code: query_code(code, timeout), workers, delay)}
        archive_results = [fresh.get(code) or prior_archive[code] for code in unresolved]
    listing_results = []
    listing_requests_this_run = 0
    needles = destination_needles(index)
    if do_listings:
        def read_listing(item):
            url = item["url"]
            if not safe_listing_url(url, hosts):
                return {**item, "outcome": "refused-unsafe-url"}
            status, body, location, error = fetch(url, timeout)
            matches = scan_listing(body, unresolved, needles) if body else {
                "short_code_hits": [], "destination_hits": [], "row_pairs": []
            }
            return {
                **item,
                "outcome": "read" if status == 200 else "http-or-transport-error",
                "http": status,
                "location_not_followed": location,
                "error": error,
                "bytes": len(body.encode("utf-8")),
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
                **matches,
            }
        prior_listings = {row["url"]: row for row in (previous or {}).get("listing_results", [])}
        todo = [item for item in listings if prior_listings.get(item["url"], {}).get("http") != 200]
        listing_requests_this_run = len(todo)
        fresh = {row["url"]: row for row in _parallel(todo, read_listing, workers, delay)}
        listing_results = [fresh.get(item["url"]) or prior_listings[item["url"]] for item in listings]
    recovered = {
        row["short_code"]: row["redirect_targets"]
        for row in archive_results if row["redirect_targets"]
    }
    return {
        "run": datetime.now(timezone.utc).isoformat(),
        "network_policy": "No bare short URLs; no redirects followed; CDX index plus explicit public listing/statistics URLs only.",
        "inputs": {"reverse_index": str(index_path), "termina_db": str(db_path)},
        "counts": {
            "unresolved_codes": len(unresolved),
            "archive_queries": len(archive_results),
            "archive_queries_this_run": archive_queries_this_run,
            "archive_codes_with_redirect_target": len(recovered),
            "public_listing_requests": sum("http" in row for row in listing_results),
            "public_listing_requests_this_run": listing_requests_this_run,
            "public_listing_row_pairs": sum(len(row.get("row_pairs", [])) for row in listing_results),
        },
        "recovered_archive_targets": recovered,
        "archive_results": archive_results,
        "listing_results": listing_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--no-cdx", action="store_true")
    parser.add_argument("--no-listings", action="store_true")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=1, help="bounded concurrent reads (default: 1)")
    parser.add_argument("--resume", action="store_true", help="reuse successful rows already present in --out")
    args = parser.parse_args()
    previous = json.loads(args.out.read_text(encoding="utf-8")) if args.resume and args.out.exists() else None
    report = run(
        args.index, args.db, do_cdx=not args.no_cdx, do_listings=not args.no_listings,
        timeout=args.timeout, delay=args.delay, workers=max(1, args.workers), previous=previous,
    )
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
