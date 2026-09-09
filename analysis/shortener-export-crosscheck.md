# Shortener / export cross-check: what we can reproduce, and what stays [reported]

This archive has been quoting two figures from the brausepulver shortener audit
([sub-swarms.md](sub-swarms.md)): **"the wiki cites 23 of 5,660 short links"** and
**"89 of the 148 target hosts the shorteners reach never appear in the wiki dump
at all."** Both were carried untagged, which let them read with the same weight as
our export counts. They are the audit's arithmetic; we had never reproduced
either.

Both are set operations over two inventories:

| Side | What it is | Reproducible here? |
|---|---|---|
| **A** | every host and shortener alias the **export** names | **yes** — the export is public and we hold it |
| **B** | every link and target host the **shorteners** hold | **no** — YOURLS statistics pages, enumerated by the audit, not re-hosted |

`scripts/shortener_export_crosscheck.py` computes A on its own and folds in B only
when a link list is supplied (`--links`). This pass ran side A.

```
python3 scripts/shortener_export_crosscheck.py --file revisions.jsonl \
    --out data/shortener_export_crosscheck_2026-09-09.json
```

Read-only, and it never resolves a short link: following a bare short URL
registers a click and mutates the very statistics being cited.

## Side A — what the export names (2026-09-09) [export]

Scanned **14,591 revisions** — identical to the archive's held count, so this is
the same corpus. Fields scanned are `body`, `name`, `page_id` and
`change_summary`; page names matter because 555 of 1,013 ProbierWiki bodies in the
export are the 27-byte placeholder.

- **204 distinct hosts** named anywhere in the export. This is the set the
  "89 of 148" test needs on our side, and it is now held in
  [`data/shortener_export_crosscheck_2026-09-09.json`](../data/shortener_export_crosscheck_2026-09-09.json).
- **152 distinct shortener aliases across 7 shortener hosts:**

| Shortener | Distinct aliases | Occurrences |
|---|---:|---:|
| `is.gd` | 48 | 309 |
| `tinyurl.com` | 42 | 304 |
| `v.gd` | 28 | 157 |
| **`vanderbi.lt`** | **25** | **260** |
| `da.gd` | 7 | 66 |
| `bitily.in` | 1 | 6 |
| `2dd.pl` | 1 | 4 |
| `uoft.me`, `rmn.re`, `yourls.pro`, `yourls.website`, `yourls.biz`, `u.ethz.ch` | **0** | 0 |

## What this settles, and what it does not

**We get 25 vanderbi.lt aliases where the audit says 23.** Not a correction — a
counting-rule difference, and the candidates are visible in the output: we resolve
the redirector form `vanderbi.lt/yourls-go.php?id=bwkug` to the alias `bwkug`
rather than to the script name, and we count the bare probe `test`. Two aliases of
difference on a figure of this size is agreement, not conflict. **The audit's
5,660 denominator is untouched by this pass**, so "23 of 5,660" is not upgraded to
"25 of 5,660": only the numerator is ours.

**The 23 figure is vanderbi.lt-only, and that is easy to misread.** Across all
shortener hosts the export cites **152** distinct aliases. This is *not* a
correction of the audit either — its 5,660 is the YOURLS cohort corpus, while
`is.gd` / `v.gd` / `tinyurl.com` / `da.gd` are the commercial fallbacks, a
different population. The point is narrower: **"the wiki cites 23 short links"
should not be quoted as "the wiki cites 23 short links"** without saying which
cohort. The wiki's shortener vocabulary is six times larger than that number
suggests.

**Two independent corroborations fall out of the zero row.**

- `rmn.re` scores **0**, which independently reproduces the finding in
  [rmn-re-verify.md](rmn-re-verify.md) that our corpus used `vanderbi.lt`, not
  `rmn.re` — previously asserted from a separate parse, now confirmed from the
  export side by a different code path.
- `uoft.me` also scores **0**, confirming it entered this archive's inventory
  through DeGraff's read of its statistics pages, never through the wiki text.

**The three DeGraff-named vanderbi.lt links are not literally cited.**
`massjson715`, `cookage85datax` and `rwandamodvals` are described in
[surfaces.md](surfaces.md) as having "wiki ties" — but none appears in the 25.
Their tie is content and page-name matching (e.g. `cookage85datax` against the
DSEWiki page `AgentCookAge85DataXYZ`), not citation. The cross-check makes that
distinction explicit rather than leaving "tie" to do the work.

## Side B — blocked here, and why

**`vanderbi.lt` is unreachable from this environment**: the egress proxy answers
`403` to `CONNECT vanderbi.lt:443` (policy denial, logged as
`connect_rejected`). The audit's own dashboard was likewise unreachable. So the
5,660-link corpus, the 148 target hosts, the 4,050 creator IPs and the 22 task
families **stay [reported]** and are now tagged as such at the point of use in
[sub-swarms.md](sub-swarms.md).

The script is built so that side B is an input, not a scrape: anyone on a network
that permits `vanderbi.lt` can export its statistics pages to JSON or CSV and run

```
python3 scripts/shortener_export_crosscheck.py --links vanderbi_links.json --out out.json
```

which prints `target_hosts`, `target_hosts_in_export`,
`target_hosts_absent_from_export` and `aliases_cited_in_export` directly — the
"89 of 148" and "23 of 5,660" answers, computed rather than quoted. Alias matching
strips the trailing `+` of a statistics-page name, and host matching folds `www.`
so the export's `www.sec.gov` and a shortener's `sec.gov` are one host.

**Do not resolve the bare short links to build that list.** Read the `+`
statistics pages, as [surfaces.md](surfaces.md) already requires; do not reproduce
the API keys in the `iyg1y` referrer log; and convert vanderbi.lt's
America/Chicago stamps before comparing them with UTC revision times.

## Disposition

- **[export]** 14,591 revisions scanned; 204 distinct hosts; 152 shortener aliases
  across 7 hosts; the per-host table above; the zero rows for `rmn.re` / `uoft.me`.
- **[reported]** Everything on side B: 5,660 links, 148 target hosts, 89 unseen,
  4,050 creator IPs, 99.7% Azure AS8075, 22 task families, the 05-12 start and
  05-29 peak.
- Tests: `scripts/test_shortener_export_crosscheck.py`, 13 fixture cases, no
  network.
