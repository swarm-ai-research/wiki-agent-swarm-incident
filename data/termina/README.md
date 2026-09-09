# Termina incident database snapshot

This directory pins the CC0
[`swarm.termina.digital` incident database](https://swarm.termina.digital/db/)
as a third structured source alongside the collusion.wiki export and Joshua
David's reconstruction.

Snapshot: schema **v9**, generated **2026-09-08T14:10:58.029664541Z**.
The complete queryable copy is [`incidents.sqlite`](incidents.sqlite); the
upstream JSON schema and manifest are kept beside it, and
[`snapshot.json`](snapshot.json) pins their local sizes and hashes.
`venue.jsonl` and
`venue_link.jsonl` remain as small convenience exports for
[`scripts/atlas_augment.py`](../../scripts/atlas_augment.py).

The database is a secondary synthesis, not another independent copy of the
primary revision bodies. Preserve its row-level status and evidence fields when
using it: `verified`, `inferred`, `reported`, and `contradicted` are not
interchangeable.

## Curated-bundle manifest recovery

The deployed about page advertises a separate curated-bundle manifest, but its
`/pub/datasets/manifest.json` target is unavailable. The manifest content was
recovered from Joshua David's public September 8 scrape and is pinned as
[`curated-datasets-manifest-2026-09-08.json`](curated-datasets-manifest-2026-09-08.json),
with source commit, blob, hash, and endpoint status in
[`curated-datasets-manifest-provenance.json`](curated-datasets-manifest-provenance.json).
The local copy adds one conventional final newline; provenance pins both the
downloaded and normalized hashes. This recovers the inventory for nine tarballs,
not the tarballs themselves; none of those bundle files is claimed as held by
this repository.

## Integrity

- `incidents.sqlite`: 50,282,496 bytes,
  SHA-256 `80bb262a52008bd9e29c4d99d17aac542cfce23d72f3bd5b3f0208414632e69e`
- `manifest.json`: SHA-256
  `38450a055b556497d0d3b3fc09a5345132cc77f3a2c4cabbee75fe8ab5eceb70`
- `schema.json`: SHA-256
  `8540de3a419cf361fdc4f18a287bf376a5cd4d85d862402bd1e726ab1dc410b8`

Verify SQLite integrity, manifest row counts, and any checked-in JSONL hashes:

```sh
python3 scripts/verify_termina_snapshot.py
```

Preview an upstream refresh without changing the pinned files:

```sh
python3 scripts/update_termina_snapshot.py --dry-run
```

The updater stages every download beside this directory, requires schema v9,
compares the manifest and schema table sets, checks SQLite integrity and table
counts, verifies retained JSONL exports, and prints table plus semantic entity
deltas. Omit `--dry-run` only after reviewing that output. All files are
validated before any destination path is replaced; each promoted file uses an
atomic same-filesystem rename.

To review two already validated pins as a status-aware feed:

```sh
python3 scripts/termina_change_feed.py \
  --before path/to/older/termina \
  --after data/termina \
  --report data/termina-change.json
```

The feed reports non-scanner claim additions/removals, claim-status and evidence
lineage changes, evidence-row changes, scanner venue coverage, row-count changes,
and verdict transitions. Repeated daily scanner rows are collapsed to the latest
observation per venue and suppressed when verdict and coverage are unchanged.
Scanner observations must remain `inferred`; the command rejects a scan claim
carrying any other evidence status.

Example read-only query:

```sh
sqlite3 -readonly data/termina/incidents.sqlite \
  'SELECT id, name, status, severity FROM incident ORDER BY period_start;'
```

Upstream download and licence details:
<https://swarm.termina.digital/db/about.html#download> and
<https://swarm.termina.digital/db/about.html#licence>.
