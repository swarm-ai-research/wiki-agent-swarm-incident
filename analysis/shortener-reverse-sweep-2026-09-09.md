# Shortener reverse index and read-only sweep — 2026-09-09

## Result

The destination-keyed offline index contains 129 unique short codes from the
Atlas graph and archived resolution ledger. It records 86 codes carrying an
observed direct resolution — one edge each, so 86 edges — reaching 30 distinct
immediate destinations, and two observed nested-shortener relationships. A
further 775 proxy-topology relationships are retained only as
`topology-candidate`: shared proxy nodes collapse multiple concrete URLs, so
reachability through one is not proof that a particular short code reached every
downstream endpoint.

Forty codes had neither an observed graph resolution nor an archived recovered
target. A read-only sweep queried the Wayback CDX index for all 40 without
requesting any short URL. CDX returned successfully for every code. Four codes
had redirect captures:

- `tinyurl.com/24rdejoe` — one 302 capture
- `tinyurl.com/2amf3sba` — one 302 capture
- `uoft.me/amass932899504` — one 301 capture
- `vanderbi.lt/iyg1y` — two 301 captures and one revisit record

The indexed rows did not retain a redirect target, so none of these captures
adds a destination. The other 36 codes had no CDX rows. This is archive absence,
not proof that the links never resolved.

The sweep also requested 24 public listing or statistics URLs explicitly named
in the Termina shortener venue registry. Twenty-one returned HTTP 200. None
placed an unresolved code and a known destination in the same table/list row, so
the sweep adds no defensible code-to-target pair. Three surfaces remained
unreadable: `app.bitily.in` presented an untrusted certificate, `ctxr.me` returned
502, and `yourls.biz` did not resolve. The alternate saved Bitily listing on
`bitily.in` returned HTTP 200.

## Artifacts

- `data/shortener_reverse_index_2026-09-09.json` — full code- and
  destination-keyed index with confidence labels and paths. Its `counts` block
  carries `relations_by_confidence`, so the observed/candidate split is visible
  without reading every row: 86 `observed`, 2 `observed-chain`, 775
  `topology-candidate`.
- `data/shortener_reverse_index_2026-09-09.csv` — flat destination-to-code rows
- `data/shortener_reverse_sweep_2026-09-09.json` — CDX and listing outcomes;
  bodies are represented only by byte counts and SHA-256 digests
- `scripts/shortener_reverse_index.py` — reproducible offline transform
- `scripts/shortener_reverse_sweep.py` — resumable guarded network sweep

## Safety boundary

The network sweep refuses bare short-code paths, never follows redirects, and
does not persist listing bodies. It queries only the Wayback CDX index and the
public listing/statistics URLs already recorded in the venue registry. It does
not hit counters, `/up` endpoints, invitation URLs, or discovered redirect
targets.
