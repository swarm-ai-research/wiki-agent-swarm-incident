"""Cross-check the fast-follow trajectory bundle against the second swarm June-18 record.

Both sources describe the same collusion.wiki export from independently obtained
bundles. This recomputes the overlapping figures and the published negative
controls without copying bodies, payloads or gem contents.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import zipfile

URL = re.compile(r"https?://[^\s\]\)\}\"'<>|]+", re.I)
# A relay wrapper carries its target inside the query string or the path.
EMBEDDED = re.compile(r"https?(?::|%3a)(?://|%2f%2f)([A-Za-z0-9.\-]+)", re.I)
PREFIXED = re.compile(r"https?://[^/]+/((?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)+)/", re.I)
WINDOW = ("2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-20")
JUNE18 = "2026-06-18"

# Receipts published in the-second-swarm/01_june18_rubygems/june18_story.md.
REPORTED_BUNDLE = {"revisions": 14585, "pages": 4577, "events": 19907, "labels": 3102}
REPORTED_DAYS = {"2026-06-16": 2603, "2026-06-17": 1297, "2026-06-18": 6543,
                 "2026-06-19": 509, "2026-06-20": 657}
REPORTED_WIKIS = {"dse": 5884, "probier": 651, "fractal": 8}
REPORTED_URL_LOAD = {"revisions_with_url": 5887, "occurrences": 95375, "outer_hosts": 117}
# F2 host census, as revisions / occurrences.
REPORTED_HOSTS = {
    "www.sec.gov": (5121, 29258), "www.investor.gov": (2849, 12319),
    "jqp.vercel.app": (1737, 8757), "code.highcharts.com": (755, 877),
    "pure.md": (215, 597), "md.succ.ai": (158, 246), "api.datausa.io": (137, 718),
    "md.dhr.wtf": (132, 132), "vanderbi.lt": (125, 247), "jsonhero.io": (108, 2107),
    "r.jina.ai": (150, 408), "markdown.new": (115, 301), "webcrawlerapi.com": (149, 584),
    "www-sec-gov.translate.goog": (44, 53), "corsproxy.io": (15, 15),
    "api.allorigins.win": (14, 34), "thingproxy.freeboard.io": (12, 12),
    "test.cors.workers.dev": (4, 6), "cors.isomorphic-git.org": (1, 1),
    "translate.google.com": (15, 15), "www.google.com": (18, 29),
    "api.census.gov": (79, 83), "investor.gov": (84, 156), "prowiki.org": (81, 229),
    "allorigins.hexlet.app": (74, 269), "www.prowiki.org": (56, 160),
    "api.usaspending.gov": (31, 61), "datausa.io": (24, 33),
    "www.hockey-reference.com": (21, 63), "da.gd": (18, 63), "api.counterapi.dev": (18, 28),
}
# F3 doors the registry exercised and the wiki, per that report, never wrote.
REGISTRY_ONLY = ("validator.w3.org", "cors.lol", "cors-proxy.htmldriven.com",
                 "search.google.com", "r-jina-ai.translate.goog",
                 "markdown-new.translate.goog")
# F4 cross-channel identity controls.
REGISTRY_TOKENS = ("rubygems", "web_hooks", "go-import", "W17162", "Q61361",
                   "gemspec", ".gem")
# fleet_anatomy_20260827.md: image-farm mirror cadence, in seconds.
CADENCE_BAND = (3455, 3460)
CADENCE_WIDE = (3400, 3520)
# Actor A artifact vocabulary, to test for any presence in the agent-incident corpus.
ACTOR_A = {
    "huggingface": r"hugging\s*?face|hf\.co|huggingface",
    "hf_accounts": r"newpc[A-Za-z0-9]{2,}|user-(?:lzathslk|qaiocbhg|unhrdtnl)",
    "rubygems": r"rubygems|gemspec",
    "attribution_names": r"darkfibr|blackfish|haddock",
    "parquet": r"parquet",
    "hdf5": r"hdf5|h5py",
}


def revision(root):
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def load_bundle(trajectories):
    archive = trajectories / "full-wiki-logs.zip"
    with zipfile.ZipFile(archive) as zf:
        raw = {name: zf.read(name) for name in zf.namelist()
               if name.endswith(".jsonl")}
    rows = {name.removesuffix(".jsonl"):
            [json.loads(line) for line in blob.splitlines() if line.strip()]
            for name, blob in raw.items()}
    digests = {name: hashlib.sha256(blob).hexdigest() for name, blob in raw.items()}
    return rows, digests, hashlib.sha256(archive.read_bytes()).hexdigest()


def durations(text):
    """Seconds implied by each duration literal an agent wrote."""
    out = []
    for h, m, s in re.findall(r"(?<![\d:])(\d{1,2}):(\d{2}):(\d{2})(?![\d])", text):
        out.append(int(h) * 3600 + int(m) * 60 + int(s))
    for h, m, s in re.findall(r"(\d{1,2})h(\d{1,2})m(\d{1,2})s", text):
        out.append(int(h) * 3600 + int(m) * 60 + int(s))
    for m, s in re.findall(r"(?<![\d.])(\d{1,3})m(\d{1,2})s", text):
        out.append(int(m) * 60 + int(s))
    out += [int(v) for v in re.findall(r"(\d{1,4})[- ]?(?:second|sec\b|s\b)", text)]
    out += [int(v) * 60 for v in re.findall(r"(\d{1,3})[- ]?minute", text)]
    return out


def crosscheck(trajectories, second_swarm):
    rows, digests, archive_sha = load_bundle(trajectories)
    revisions = rows["revisions"]
    results = {
        "trajectories_commit": revision(trajectories),
        "second_swarm_commit": revision(second_swarm),
        "bundle": {
            "archive_sha256": archive_sha,
            "file_sha256": digests,
            "counts": {name: len(rs) for name, rs in rows.items()},
            "reported_counts": REPORTED_BUNDLE,
            "offsets": {k: len(rows[k]) - v for k, v in REPORTED_BUNDLE.items()},
        },
    }

    by_day = collections.Counter(r["write_date"][:10] for r in revisions)
    results["daily"] = {
        "recomputed": {d: by_day[d] for d in WINDOW},
        "reported": REPORTED_DAYS,
        "matches": all(by_day[d] == REPORTED_DAYS[d] for d in WINDOW),
        "full_range": dict(sorted(by_day.items())),
    }

    june18 = [r for r in revisions if r["write_date"][:10] == JUNE18]
    wikis = collections.Counter(r["wiki"] for r in june18)
    results["june18_wikis"] = {
        "recomputed": dict(wikis.most_common()),
        "reported": REPORTED_WIKIS,
        "matches": dict(wikis) == REPORTED_WIKIS,
    }
    pages = collections.Counter(r["page_id"] for r in june18)
    results["june18_top_pages"] = dict(pages.most_common(5))

    # The report attributes each URL to a host but does not ship the rule, so
    # score it both ways: the outer host alone, and the outer host plus every
    # target a relay wrapper carries.
    counts = {mode: (collections.Counter(), collections.defaultdict(set))
              for mode in ("outer", "effective")}
    with_url = 0
    for r in june18:
        found = URL.findall(r.get("body") or "")
        with_url += bool(found)
        for u in found:
            outer = (urllib.parse.urlparse(u).hostname or "").lower()
            decoded = urllib.parse.unquote(urllib.parse.unquote(u))
            prefixed = PREFIXED.match(decoded)
            effective = [outer] if outer else []
            effective += [h.lower() for h in EMBEDDED.findall(decoded)[1:]]
            if prefixed:
                effective.append(prefixed.group(1).lower())
            for mode, hosts in (("outer", [outer] if outer else []),
                                ("effective", list(dict.fromkeys(effective)))):
                occurrences, host_revs = counts[mode]
                for host in hosts:
                    occurrences[host] += 1
                    host_revs[host].add(r["rev_id"])
    results["june18_url_load"] = {
        "recomputed": {"revisions_with_url": with_url,
                       "occurrences": sum(counts["outer"][0].values()),
                       "outer_hosts": len(counts["outer"][0])},
        "reported": REPORTED_URL_LOAD,
    }

    census = {}
    agree = {"outer": 0, "effective": 0}
    for host, claim in REPORTED_HOSTS.items():
        entry = {"reported": list(claim)}
        for mode in ("outer", "effective"):
            occurrences, host_revs = counts[mode]
            got = [len(host_revs.get(host, ())), occurrences.get(host, 0)]
            entry[mode] = got
            agree[mode] += tuple(got) == claim
        entry["reproduced_by"] = [m for m in ("outer", "effective")
                                  if tuple(entry[m]) == claim]
        census[host] = entry
    results["june18_host_census"] = {
        "hosts": census, "exact_matches": agree, "checked": len(REPORTED_HOSTS),
        "reproduced_by_either": sum(bool(e["reproduced_by"]) for e in census.values())}
    results["registry_only_doors"] = {
        host: {mode: [len(counts[mode][1].get(host, ())), counts[mode][0].get(host, 0)]
               for mode in ("outer", "effective")}
        for host in REGISTRY_ONLY}

    pairing = second_swarm / "01_june18_rubygems" / "pairing_30s.csv"
    with pairing.open() as fh:
        gems = sorted({row["gem"] for row in csv.DictReader(fh)})
    june18_bodies = "\n".join(r.get("body") or "" for r in june18).lower()
    export_bodies = "\n".join(r.get("body") or "" for r in revisions).lower()
    results["cross_channel_controls"] = {
        "pairing_csv_sha256": hashlib.sha256(pairing.read_bytes()).hexdigest(),
        "distinct_gem_names": len(gems),
        "gem_names_in_june18_bodies": [g for g in gems if g.lower() in june18_bodies],
        "gem_names_in_whole_export": [g for g in gems if g.lower() in export_bodies],
        "registry_tokens": {t: [export_bodies.count(t.lower()),
                                june18_bodies.count(t.lower())]
                            for t in REGISTRY_TOKENS},
    }

    data = trajectories / "trajectory-explorer" / "public" / "data"
    literals, gaps = [], []
    for path in sorted((data / "assembled-trajectories").glob("*.json")):
        dossier = json.loads(path.read_text())
        for claim in dossier.get("schedule_evidence", []):
            literals += durations(f"{claim.get('value', '')} {claim.get('excerpt', '')}")
        stamps = sorted(m["utc"] for m in dossier.get("owned_messages", []))
        times = [int(__import__("datetime").datetime.fromisoformat(
            s.replace("Z", "+00:00")).timestamp()) for s in stamps]
        gaps += [b - a for a, b in zip(times, times[1:])]
    literals += durations((data / "assembled-environment.json").read_text())
    band = lambda xs, lo, hi: sorted(x for x in xs if lo <= x <= hi)
    results["cadence"] = {
        "band_seconds": list(CADENCE_BAND),
        "schedule_literals": len(literals),
        "literals_in_band": band(literals, *CADENCE_BAND),
        "literals_in_wide_band": band(literals, *CADENCE_WIDE),
        "observed_gaps": len(gaps),
        "gaps_in_band": band(gaps, *CADENCE_BAND),
        "gaps_in_wide_band": band(gaps, *CADENCE_WIDE),
        "median_gap_seconds": sorted(gaps)[len(gaps) // 2] if gaps else None,
        "common_cooldowns": [[v, n] for v, n in collections.Counter(
            x for x in literals if 300 <= x <= 7200).most_common(12)],
    }

    corpus = export_bodies + (data / "assembled-environment.json").read_text().lower()
    results["actor_a_vocabulary"] = {
        name: len(re.findall(pattern, corpus, re.I))
        for name, pattern in ACTOR_A.items()}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectories", type=Path,
                        help="checkout of fast-follow-question-trajectories")
    parser.add_argument("second_swarm", type=Path,
                        help="checkout of darkfibr/the-second-swarm")
    args = parser.parse_args()
    print(json.dumps(crosscheck(args.trajectories, args.second_swarm), indent=1))


if __name__ == "__main__":
    main()
