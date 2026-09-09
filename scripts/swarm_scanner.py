#!/usr/bin/env python3
"""Generic scan for agent-swarm activity on open-edit wikis.

Unlike wiki_lookup.py (which looks for THIS swarm's fingerprints), this scores a
wiki's RecentChanges for swarm-like behaviour of any origin:

  cloud     editors resolving to cloud/VPS reverse DNS (azure, ec2, ovh, hetzner…)
  handles   CamelCase handles ending in Agent/Bot/Helper/Researcher/Fleet/Relay…
  words     automation vocabulary in summaries (agent, fleet, swarm, payload,
            round N, deadline, editability test, LLM/GPT/Claude/OpenAI…)
  payload   base64 runs and JSON envelopes in visible text
  infra     counters, webhooks, fetch proxies, pastes, shorteners in URLs
  burst     max edits/day in 2025-26 vs the median active day

Input is a JSON list of {name, engine, urls} (WikiIndex export shape) or --url
targets. Output: a versioned JSON run envelope with provenance, hashes, score
inputs, and ranked results, plus a table of the top hits. Page excerpts are
omitted unless explicitly requested. Read-only, one request per wiki.

  python3 scripts/swarm_scanner.py --input wikiindex_openedit.json --skip-engines Wikia,Fandom --out data/swarm_scan.json
  python3 scripts/swarm_scanner.py --url https://example.org/wiki.pl?action=rc
  python3 scripts/swarm_scanner.py --report data/swarm_scan.json --top 30
  python3 scripts/swarm_scanner.py --calibrate
"""
import argparse, collections, hashlib, json, re, statistics, subprocess, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import partial
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_lookup as W  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA_VERSION = 2
FIXTURE_MANIFEST = ROOT / "data" / "scanner_fixtures" / "manifest.json"
FETCH_TIMEOUT = 20

SIG = {
    "cloud": re.compile(r"azure|amazonaws|ec2-\d|compute\.internal|googleusercontent|\.ovh\.|ip-\d+-\d+-\d+|hetzner|digitalocean|linode|vultr|contabo|scaleway|\b(?:20|40|52|104|172\.1[6-9]|172\.2\d|172\.3[01])\.\d+\.\d+\.\d+\b", re.I),
    "handles": re.compile(r"\b(?:[A-Z][a-z]+){1,3}(?:Agent|Helper|Researcher|Worker|Fleet|Swarm|Relay|Probe|Bridge|Scout|Runner|Tester)[A-Za-z0-9]*\b"),
    "bots": re.compile(r"\b(?:[A-Z][a-z]+){1,3}Bot[A-Za-z0-9]*\b"),
    "words": re.compile(r"\b(?:automated|autonomous|agents?|fleet|swarm|relay|envelope|payload|checkpoint|round \d+|deadline|task \d+|sandbox test|editability test|LLM|GPT-?\d|Claude|OpenAI|Anthropic|Gemini)\b", re.I),
    "payload": re.compile(r"[A-Za-z0-9+/]{120,}={0,2}|\{\s*\"v\"\s*:\s*\d|\"payload\"\s*:"),
    "infra": re.compile(r"counterapi|countapi|ntfy\.sh|webhook\.site|r\.jina\.ai|markdown\.new|allorigins|corsproxy|cors\.[a-z]+\.workers|jqp\.vercel|pastebin|paste\.|is\.gd|tinyurl|v\.gd|da\.gd|md\.succ", re.I),
}
# Any-year date headers, so old rows segment under their OWN (old) header instead
# of leaking into a recent one; only 2025–26 buckets are counted (RECENT_YEARS).
_M = "January|February|March|April|May|June|July|August|September|October|November|December"
_DM = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
DATES = [
    re.compile(r"\b((?:19|20)\d\d)-(\d\d)-(\d\d)\b"),
    re.compile(r"\b(?:" + _M + r") \d{1,2},? (?:19|20)\d\d\b"),
    re.compile(r"\b\d{1,2}\. (?:" + _DM + r") (?:19|20)\d\d\b"),
    re.compile(r"\b\d{1,2}\.\d{1,2}\.((?:19|20)\d\d)\b"),
    re.compile(r"\b\d{1,2} (?:" + _M + r") (?:19|20)\d\d\b"),
]
TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
RECENT_YEARS = {2025, 2026}
MONTHS = {name: i for names in (_M, _DM)
          for i, name in enumerate(names.split("|"), 1)}


def _parse_day(daykey):
    """Normalize all supported headers, rejecting impossible calendar dates."""
    parts = daykey.replace(",", "").replace(".", " ").split()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", daykey):
            return date.fromisoformat(daykey)
        if parts[0] in MONTHS:
            month, day, year = MONTHS[parts[0]], int(parts[1]), int(parts[2])
        else:
            day, year = int(parts[0]), int(parts[2])
            month = MONTHS[parts[1]] if parts[1] in MONTHS else int(parts[1])
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _countable(daykey):
    """Only valid, non-future dates in the incident's 2025–26 window count."""
    day = _parse_day(daykey)
    return day is not None and day.year in RECENT_YEARS and day <= date.today()
WEIGHTS = {"cloud": 2, "handles": 3, "bots": 0.2, "words": 1.5, "payload": 4, "infra": 2}
CAPS = {"cloud": 10, "handles": 10, "bots": 10, "words": 10, "payload": 5, "infra": 10}
ENGINE_RC = {
    "mediawiki": "/Special:RecentChanges?days=30&limit=500",
    "usemod": "?action=rc&days=200&all=1",
    "oddmuse": "?action=rc;days=200;all=1",
    "prowiki": "?action=browse&id=RecentChanges&days=200&all=1",
    "pmwiki": "?n=Site.AllRecentChanges",
    "moinmoin": "/RecentChanges",
    "dokuwiki": "?do=recent",
    "phpwiki": "/RecentChanges",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scanner_identity():
    """Identify the exact scanner bytes; git metadata is supplemental."""
    path = Path(__file__).resolve()
    identity = {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "git_commit": None,
        "git_dirty": None,
    }
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", identity["path"]], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        identity.update(git_commit=commit, git_dirty=dirty)
    except (OSError, subprocess.CalledProcessError):
        pass
    return identity


def scoring_config():
    return {
        "weights": WEIGHTS,
        "caps": CAPS,
        "signals": {name: {"pattern": rx.pattern, "flags": rx.flags} for name, rx in SIG.items()},
        "recent_years": sorted(RECENT_YEARS),
        "burst_cap": 20,
    }


def engine_key(e):
    e = (e or "").lower()
    for k in ("usemod", "oddmuse", "prowiki", "pmwiki", "moinmoin", "dokuwiki", "phpwiki", "mediawiki"):
        if k in e.replace(" ", ""):
            return k
    return "mediawiki" if "wiki" in e else "unknown"


def rc_from(engine, url):
    p = urllib.parse.urlsplit(url)
    original_query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)

    def endpoint(path, query):
        # Keep installation selectors such as wiki=foo; replace page/action
        # selectors and scan-window controls with the RC request's values.
        controls = {"title", "id", "n", "do", "action", "days", "limit", "all"}
        kept = [(k, v) for k, v in original_query if k not in controls]
        kept.extend(urllib.parse.parse_qsl(query.replace(";", "&"), keep_blank_values=True))
        return urllib.parse.urlunsplit((p.scheme, p.netloc, path,
                                       urllib.parse.urlencode(kept), ""))

    if engine == "mediawiki":
        # Statistics URL is usually .../index.php?title=Special:Statistics or /wiki/Special:Statistics
        if any(k == "title" for k, _ in original_query) or p.path.endswith(".php"):
            return endpoint(p.path, "title=Special:RecentChanges&days=30&limit=500")
        path = re.sub(r"/Special:.*$", "", p.path)
        return endpoint(path.rstrip("/") + "/Special:RecentChanges", "days=30&limit=500")
    m = re.match(r"(.*?\.(?:pl|cgi|php|py))(?:[/?].*)?$", p.path)
    suffix = ENGINE_RC.get(engine, "?action=rc" if m else "/RecentChanges")
    rc_path, _, query = suffix.partition("?")
    if m:
        return endpoint(m.group(1) + rc_path, query)
    path = re.sub(r"/[^/]*$", "", p.path)
    return endpoint(path + rc_path, query)


def installation_key(url):
    """Compare equivalent RC URLs without merging sibling installations."""
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    default_port = ":443" if p.scheme.lower() == "https" else ":80"
    if host.endswith(default_port):
        host = host[:-len(default_port)]
    return (p.scheme.lower(), host, p.path or "/",
            tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True))))


def build_targets(entries, skip, statuses):
    targets, seen = [], set()
    for entry in entries:
        if ((entry.get("engine") or "").lower() in skip
                or entry.get("status", "") not in statuses or not entry.get("urls")):
            continue
        engine = engine_key(entry.get("engine"))
        url = rc_from(engine, entry["urls"][0])
        key = installation_key(url)
        if key not in seen:
            seen.add(key)
            targets.append({"name": entry["name"], "engine": engine, "url": url})
    return targets


def requested_window(url, fetched_at):
    """Record requested controls and their UTC calendar interpretation."""
    query = urllib.parse.urlsplit(url).query.replace(";", "&")
    params = urllib.parse.parse_qs(query)
    controls = {key: values[-1] for key, values in params.items()
                if key in {"days", "from", "upto", "limit", "all"}}
    fetched_day = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).date()
    window = {"parameters": controls, "from": None, "to": fetched_day.isoformat()}
    try:
        if "days" in controls:
            window["from"] = (fetched_day - timedelta(days=int(controls["days"]))).isoformat()
        elif "from" in controls:
            window["from"] = datetime.fromtimestamp(
                int(controls["from"]), timezone.utc
            ).date().isoformat()
        if "upto" in controls:
            window["to"] = datetime.fromtimestamp(
                int(controls["upto"]), timezone.utc
            ).date().isoformat()
    except (ValueError, OverflowError):
        window["interpretation_error"] = True
    return window


def observed_window(days):
    keys = sorted(days)
    return {
        "from": keys[0] if keys else None,
        "to": keys[-1] if keys else None,
        "active_days": len(keys),
        "counted_rows": sum(days.values()),
        "basis": "counted_structural_rows",
    }


def day_counts(text):
    """Per-day edit-row counts, robust to both RecentChanges layouts.

    Older versions counted date-token occurrences, which equals rows only when
    each row carries its own date (inline, as some Oddmuse skins do). MediaWiki
    and UseMod print the date once as a day header and put a time on each row, so
    token-counting saw ~1 per day and the burst signal went blind. Here every
    HH:MM row is bucketed under its preceding day header. Supported dates share
    ISO day keys; invalid, future, and out-of-window headers separate rows but
    do not contribute counts.
    """
    import bisect
    headers = sorted((m.start(), m.group(0)) for rx in DATES for m in rx.finditer(text))
    times = [m.start() for m in TIME.finditer(text)]
    if headers and len(times) > len(headers):
        hpos = [h[0] for h in headers]
        counts = collections.Counter()
        for t in times:
            i = bisect.bisect_right(hpos, t) - 1
            if i >= 0 and _countable(headers[i][1]):   # old-year buckets drop out here
                counts[_parse_day(headers[i][1]).isoformat()] += 1
        if counts:
            return counts
    return collections.Counter(_parse_day(d).isoformat()
                               for _, d in headers if _countable(d))


class _EditRowParser(HTMLParser):
    """Count only structural RC rows for the three supported HTML families."""

    def __init__(self, engine):
        super().__init__(convert_charrefs=True)
        self.engine = engine
        self.container_nesting = 0
        self.container_seen = False
        self.in_p = 0
        self.in_li = 0
        self.in_header = False
        self.header_text = []
        self.current_day = None
        self.counts = collections.Counter()
        self.rows_seen = 0
        self.headers_seen = 0
        self.identities = set()

    @property
    def in_container(self):
        return self.container_nesting > 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "div":
            if self.in_container:
                self.container_nesting += 1
            elif ((self.engine == "usemod" and "wikirc" in classes)
                  or (self.engine == "oddmuse" and "rc" in classes)):
                self.container_nesting = 1
                self.container_seen = True

        if self.engine == "mediawiki" and "mw-changeslist-line" in classes:
            identity = attrs.get("data-mw-revid") or attrs.get("data-mw-logid")
            timestamp = attrs.get("data-mw-ts", "")
            # Enhanced MediaWiki wraps its real rows in a second line element.
            # Revision/log ids distinguish those rows from the wrapper.
            if identity and re.fullmatch(r"\d{14}", timestamp):
                key = (identity, timestamp)
                if key not in self.identities:
                    self.identities.add(key)
                    self.rows_seen += 1
                    daykey = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
                    if _countable(daykey):
                        self.counts[daykey] += 1
            return

        if not self.in_container:
            return
        if tag == "p":
            self.in_p += 1
        elif tag == "li":
            # UseMod and Oddmuse emit HTML4-style <li> starts without </li>.
            self.in_li = 1
            self.rows_seen += 1
            if (self.current_day is not None and self.current_day.year in RECENT_YEARS
                    and self.current_day <= date.today()):
                self.counts[self.current_day.isoformat()] += 1
        elif tag in ("strong", "b") and self.in_p and not self.in_li:
            self.in_header = True
            self.header_text = []

    def handle_data(self, data):
        if self.in_header:
            self.header_text.append(data)

    def handle_endtag(self, tag):
        if self.in_container:
            if tag in ("strong", "b") and self.in_header:
                self.in_header = False
                day = _parse_day(" ".join(self.header_text).strip())
                if day is not None:
                    self.headers_seen += 1
                    self.current_day = day
            elif tag == "li" and self.in_li:
                self.in_li -= 1
            elif tag == "ul":
                self.in_li = 0
            elif tag == "p" and self.in_p:
                self.in_p -= 1
        if tag == "div" and self.in_container:
            self.container_nesting -= 1


def edit_row_counts(body, engine):
    """Return structural edit counts and explicit parser coverage metadata."""
    detected = engine
    if detected == "unknown":
        if re.search(r'class=["\'][^"\']*\bmw-changeslist\b', body, re.I):
            detected = "mediawiki"
        elif re.search(r'class=["\'][^"\']*\bwikirc\b', body, re.I):
            detected = "usemod"
        elif re.search(r'class=["\'][^"\']*\brc\b', body, re.I):
            detected = "oddmuse"
    if detected not in {"mediawiki", "usemod", "oddmuse"}:
        return collections.Counter(), {"row_parser": None, "row_parse_outcome": "unsupported_engine",
                                       "edit_rows_seen": 0, "edit_rows_counted": 0}

    parser = _EditRowParser(detected)
    parser.feed(body)
    if detected == "mediawiki":
        layout_seen = bool(re.search(r'class=["\'][^"\']*\bmw-changeslist\b', body, re.I))
    else:
        layout_seen = parser.container_seen
    if parser.rows_seen:
        outcome = "parsed"
    elif layout_seen:
        outcome = "empty"
    else:
        outcome = "unrecognized_layout"
    return parser.counts, {"row_parser": detected, "row_parse_outcome": outcome,
                           "edit_rows_seen": parser.rows_seen,
                           "edit_rows_counted": sum(parser.counts.values())}


def score_text(text, days=None, include_evidence=False):
    sig = {k: len(rx.findall(text)) for k, rx in SIG.items()}
    days = day_counts(text) if days is None else days
    vals = sorted(days.values())
    burst = (vals[-1] / max(1.0, statistics.median(vals))) if vals else 0.0
    score = sum(WEIGHTS[k] * min(sig[k], CAPS[k]) for k in WEIGHTS) + min(burst, 20)
    ev = []
    if include_evidence:
        for k, rx in SIG.items():
            for m in list(rx.finditer(text))[:2]:
                a, b = max(0, m.start() - 70), min(len(text), m.end() + 70)
                ev.append(f"[{k}] …{text[a:b].strip()}…")
    return {"score": round(score, 1), "signals": sig, "burst": round(burst, 1), "days_2526": len(days),
            "max_day": (list(max(days.items(), key=lambda x: x[1])) if days else None),
            "day_counts": dict(sorted(days.items())), "evidence": ev[:8]}


def rescore_result(result, config=None):
    """Reproduce score-derived fields from persisted counts, without page bodies."""
    config = config or scoring_config()
    signals = result.get("signals", {})
    days = result.get("day_counts", {})
    values = sorted(days.values())
    burst = values[-1] / max(1.0, statistics.median(values)) if values else 0.0
    score = sum(
        config["weights"][name] * min(signals.get(name, 0), config["caps"][name])
        for name in config["weights"]
    ) + min(burst, config["burst_cap"])
    return {
        "score": round(score, 1),
        "burst": round(burst, 1),
        "days_2526": len(days),
        "max_day": list(max(days.items(), key=lambda item: item[1])) if days else None,
    }


def scan_response(t, code, body, fetched_at=None, include_evidence=False):
    name, engine, url = t["name"], t["engine"], t["url"]
    fetched_at = fetched_at or utc_now()
    r = {
        "name": name,
        "engine": engine,
        "url": url,
        "http": code,
        "bytes": len(body.encode("utf-8")),
        "fetched_at": fetched_at,
        "content_sha256": sha256_text(body),
        "content_hash_basis": "utf-8 after fetch decode(errors=ignore)",
        "requested_window": requested_window(url, fetched_at),
    }
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    days, row_coverage = edit_row_counts(body, engine)
    r["observed_window"] = observed_window(days)
    text = W.strip(body)
    reason = W.blocked_reason(code, text, url)
    if code in (401, 402, 403, 429) or reason in ("botcheck", "tarpit"):
        outcome = "blocked"
    elif code != 200:
        outcome = "unavailable"
    elif not W.RC_MARKERS.search(body) and not W.RC_MARKERS.search(text):
        outcome, reason = "parsing_failed", "no-rc-structure"
    else:
        outcome, reason = "readable", None
    r.update(outcome=outcome, reason=reason,
             blocked=reason if outcome == "blocked" else None,
             bot_check=reason in ("http402", "botcheck"),
             score=None, signals={}, evidence=[])
    r.update(row_coverage)
    if outcome == "readable":
        r.update(score_text(text, days, include_evidence=include_evidence))
    return r


def scan_one(t, include_evidence=False):
    code, body = W.fetch(t["url"], FETCH_TIMEOUT)
    return scan_response(t, code, body, include_evidence=include_evidence)


def input_metadata(path, entries):
    if not path:
        return {"kind": "urls", "count": len(entries), "sha256": sha256_text(
            json.dumps(entries, sort_keys=True, separators=(",", ":"))
        )}
    source = Path(path)
    return {
        "kind": "wikiindex-json",
        "path": str(source),
        "count": len(entries),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def run_envelope(results, args, targets, source, started_at, completed_at=None, state="complete"):
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "run": {
            "state": state,
            "started_at": started_at,
            "completed_at": completed_at,
            "scanner": scanner_identity(),
            "configuration": {
                "workers": args.workers,
                "limit": args.limit,
                "skip_engines": args.skip_engines,
                "statuses": args.statuses,
                "fetch_timeout_seconds": FETCH_TIMEOUT,
                "include_evidence": args.include_evidence,
                "scoring": scoring_config(),
            },
            "source": source,
            "targets_sha256": sha256_text(json.dumps(targets, sort_keys=True, separators=(",", ":"))),
            "target_count": len(targets),
        },
        "results": results,
    }


def result_rows(payload):
    """Unwrap v2 output while retaining historical list compatibility."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    raise ValueError("scanner report must be a historical result list or a v2 result envelope")


def run_calibration(manifest_path=FIXTURE_MANIFEST):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    for fixture in manifest["fixtures"]:
        body = (manifest_path.parent / fixture["file"]).read_text(encoding="utf-8")
        target = {"name": fixture["id"], "engine": fixture["engine"], "url": fixture["url"]}
        result = scan_response(
            target, fixture.get("http", 200), body,
            fetched_at=manifest["fixture_time"], include_evidence=False,
        )
        expected = fixture["expected"]
        passed = result["outcome"] == expected["outcome"]
        if "min_score" in expected:
            passed = passed and result["score"] >= expected["min_score"]
        if "max_score" in expected:
            passed = passed and result["score"] <= expected["max_score"]
        result.update(fixture_id=fixture["id"], expected=expected, calibration_passed=passed)
        results.append(result)
    return {"schema_version": 1, "authored_fixtures": True, "results": results}


def coverage_outcome(result):
    """Legacy exports cannot prove that a successful response was an RC page."""
    if result.get("outcome"):
        return result["outcome"]
    if result.get("http") in (401, 402, 403, 429) or result.get("bot_check"):
        return "blocked"
    if result.get("http") != 200:
        return "unavailable"
    if result.get("blocked") == "no-rc-structure":
        return "parsing_failed"
    if result.get("blocked"):
        return "blocked"
    return "legacy_unverified"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="JSON list of {name, engine, urls}")
    ap.add_argument("--url", action="append", help="scan this RecentChanges URL directly (repeatable)")
    ap.add_argument("--skip-engines", default="Wikia,Fandom", help="comma list of engine names to skip")
    ap.add_argument("--statuses", default="Active,Vibrant,New,Needs love,", help="WikiIndex statuses to include")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="max wikis to scan (0 = all)")
    ap.add_argument("--out", default="swarm_scan.json")
    ap.add_argument("--report", help="print the table from this results file and exit")
    ap.add_argument("--include-evidence", action="store_true",
                    help="include short page excerpts (off by default; hashes/counts suffice for rescoring)")
    ap.add_argument("--calibrate", nargs="?", const=str(FIXTURE_MANIFEST),
                    help="run authored offline fixtures (optionally provide a manifest path)")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    if a.report:
        report(json.loads(Path(a.report).read_text()), a.top)
        return
    if a.calibrate:
        calibration = run_calibration(a.calibrate)
        for row in calibration["results"]:
            print(f"{'PASS' if row['calibration_passed'] else 'FAIL'} {row['fixture_id']}: "
                  f"{row['outcome']} score={row.get('score')}")
        if not all(row["calibration_passed"] for row in calibration["results"]):
            raise SystemExit(1)
        return
    started_at = utc_now()
    targets = []
    if a.url:
        targets = [{"name": u, "engine": "unknown", "url": u} for u in a.url]
        source = input_metadata(None, targets)
    else:
        entries = json.loads(Path(a.input).read_text())
        skip = {s.strip().lower() for s in a.skip_engines.split(",") if s.strip()}
        st = {s.strip() for s in a.statuses.split(",")}
        targets = build_targets(entries, skip, st)
        source = input_metadata(a.input, entries)
    if a.limit:
        targets = targets[: a.limit]
    print(f"scanning {len(targets)} wikis", file=sys.stderr)
    results = []
    worker = partial(scan_one, include_evidence=a.include_evidence)
    with ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(worker, targets), 1):
            results.append(r)
            if i % 100 == 0:
                print(f"  {i}/{len(targets)}", file=sys.stderr)
                payload = run_envelope(results, a, targets, source, started_at, state="partial")
                Path(a.out).write_text(json.dumps(payload, indent=2) + "\n")
    results.sort(key=lambda r: -(r.get("score") or 0))
    payload = run_envelope(results, a, targets, source, started_at, utc_now())
    Path(a.out).write_text(json.dumps(payload, indent=2) + "\n")
    report(payload, a.top)


def report(payload, top):
    results = result_rows(payload)
    if isinstance(payload, dict) and payload.get("run"):
        run = payload["run"]
        scanner = run.get("scanner", {})
        print(f"run schema v{payload.get('schema_version')}: {run.get('state', 'unknown')}; "
              f"scanner {scanner.get('sha256', 'unknown')[:12]}; started {run.get('started_at', 'unknown')}")
        config = run.get("configuration", {}).get("scoring") or scoring_config()
        rescorable = [r for r in results if r.get("score") is not None and "day_counts" in r]
        matches = sum(rescore_result(r, config)["score"] == r["score"] for r in rescorable)
        print(f"offline rescore: {matches}/{len(rescorable)} stored scores match")
    coverage = collections.Counter(coverage_outcome(r) for r in results)
    live = [r for r in results if coverage_outcome(r) == "readable"]
    print(f"{len(results)} scanned: " + ", ".join(
        f"{coverage[k]} {k}" for k in
        ("readable", "blocked", "unavailable", "parsing_failed", "legacy_unverified")))
    print("Scores rank readable pages only; unreadable or unverified coverage is not a negative finding.")
    row_coverage = collections.Counter(r.get("row_parse_outcome", "not_recorded") for r in live)
    if live:
        print("Edit-row parsing: " + ", ".join(
            f"{row_coverage[k]} {k}" for k in
            ("parsed", "empty", "unrecognized_layout", "unsupported_engine", "not_recorded")
            if row_coverage[k]))
    if coverage["legacy_unverified"]:
        print("Legacy scores remain in the JSON; RC coverage was not verified by that export.")
    print(f"{'score':>5} {'name':30s} {'engine':9s} {'burst':>5} {'days':>4}  signals")
    for r in sorted(live, key=lambda r: -r.get("score", 0))[:top]:
        print(f"{r['score']:5.1f} {r['name'][:30]:30s} {r['engine']:9s} {r.get('burst',0):5.1f} {r.get('days_2526',0):4d}  {r.get('signals')}")
    print("\nevidence for top 8:")
    for r in sorted(live, key=lambda r: -r.get("score", 0))[:8]:
        print(f"-- {r['name']}  {r['url']}")
        for e in r.get("evidence", [])[:5]:
            print("    ", e[:220])


if __name__ == "__main__":
    main()
