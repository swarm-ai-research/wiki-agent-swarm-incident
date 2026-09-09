# September 8 scanner top-15 triage

**Result: no new confirmed host.** A focused second fetch retained short evidence
excerpts for the 15 highest-scoring readable endpoints from the schema-v2
population run. All 15 responses were readable and all 15 scores reproduced
offline.

| Disposition | Count | Interpretation |
| --- | ---: | --- |
| Known incident-related surface | 2 | DorfWiki and TextEditors; neither is a new discovery |
| False positive | 6 | Lexical collisions or ordinary bot activity explain the signal |
| Detector-only unresolved | 7 | Burst score with no attributable cloud, handle, vocabulary, payload, or infrastructure signal |
| New confirmed host | 0 | No lead crossed an attribution threshold |

The false positives are instructive. ApfelWiki scored on old pages containing
`Azureus`, the German compound `Agentursoftware`, and `WineBottler`. Punjabi
Wikipedia's lone handle hit was `FrontRunner` in transit-localization text.
Wikidata Test, Arabic Wikiversity, and West-Vlamschn Wikipedia exposed ordinary
named-bot maintenance. Tibetan Wikipedia matched instructions for requesting a
bot flag. These are content-level explanations, not merely low scores.

Seven Wikimedia-family leads scored on burst alone. They remain detector-only
unknowns rather than negative findings, but a capped generic burst with every
attribution-bearing signal at zero is insufficient to promote a host.

Artifacts:

- [`data/swarm_scan_top15_manifest_2026-09-08.json`](../data/swarm_scan_top15_manifest_2026-09-08.json)
- [`data/swarm_scan_top15_evidence_2026-09-08.json`](../data/swarm_scan_top15_evidence_2026-09-08.json)
- [`data/swarm_scan_top15_triage_2026-09-08.json`](../data/swarm_scan_top15_triage_2026-09-08.json)

The evidence scan includes short excerpts by explicit opt-in; it does not retain
whole third-party page bodies.
