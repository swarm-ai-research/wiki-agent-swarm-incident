# Termina distinct-write estimator methodology

**Snapshot date:** 2026-09-08. **Schema:** v9. **Status:** measurement method,
not an attribution claim.

The [Termina glossary](https://swarm.termina.digital/db/about.html#glossary)
distinguishes observations from writes. A revision, its RecentChanges entry,
and its save event can be three observations of one write. It defines writes as
distinct saves, using revisions where an export supplies them and one row per
listing entry otherwise; it separately says a save row pairs with a revision.

The campaign analysis therefore publishes three deterministic values:

- **Lower:** every revision plus each distinct paste or shortlink external ID.
  RecentChanges stand-ins are omitted. This is a strong-identity floor, not an
  estimate of all activity.
- **Central:** lower plus RecentChanges rows only for campaign/venue pairs with
  no revision stream. This follows the glossary and remains the value exposed as
  `estimated_writes` for compatibility.
- **Upper:** central plus every RecentChanges row beside revision streams. This
  is an unlinked-observation ceiling; it deliberately ignores the likely overlap
  to show how much the database cannot pair row by row.

All three deduplicate paste and shortlink rows by campaign, venue, kind and
`external_id`, falling back to the record ID. All exclude `save`, `delete`, and
`revert`. Repeated revision bodies are not deduplicated because a saved revision
is a write even when its content repeats.

| Campaign | Lower | Central | Upper |
|---|---:|---:|---:|
| `artifactory-swarm` | 0 | 0 | 0 |
| `paste-qa-early` | 35 | 35 | 35 |
| `swarm-cohort` | 5,220 | 5,236 | 9,349 |
| `swarm-retrieval` | 7,104 | 7,136 | 10,743 |
| `usemod-fleet` | 6 | 6 | 18 |
| `venue-probes` | 188 | 188 | 188 |
| `visitors` | 18 | 73 | 73 |
| `xinzhai-2026-07` | 3,574 | 3,574 | 3,574 |

The largest spreads occur on DSEWiki, where both revision exports and
RecentChanges observations exist. The lower-to-central gap instead identifies
listing-only venues: those rows are the best available timeline but lack held
revision bodies. These are coverage bounds, not confidence intervals, and they
do not address whether Termina's campaign assignment is correct.

Exact campaign and venue values are generated in
[`data/termina_campaign_analysis_2026-09-08.json`](../data/termina_campaign_analysis_2026-09-08.json)
by [`scripts/termina_campaign_analysis.py`](../scripts/termina_campaign_analysis.py).
