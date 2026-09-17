# Scheduled read-only shortener monitoring

`scripts/shortener_reverse_monitor.py` turns the existing reverse index and
guarded sweep into a recurring, reviewable observation stream. It detects:

- newly indexed short codes;
- changed SHA-256 digests on registry-listed public listing/statistics pages;
- newly indexed Wayback CDX captures; and
- transport failures against those permitted surfaces.

The monitor stores one immutable JSON manifest per run. A manifest contains
timestamps, HTTP status, byte counts, hashes, sanitized URLs, CDX metadata and
stable event IDs. It never stores a response body or a full exception message.
Sensitive query values are redacted.

## Safety boundary

All network access remains in `scripts/shortener_reverse_sweep.py`. The sweep
uses a no-redirect opener and permits only Wayback CDX plus listing/statistics
URLs explicitly recorded in the Termina venue registry. It refuses bare short
codes and counter hosts. `/up` endpoints, invitation pages, lure destinations
and discovered redirect targets do not satisfy the listing allowlist and are
never requested. Monitor output is not fed back into the request set.

## Review-first operation

Run into an out-of-repository staging directory:

```bash
python3 scripts/shortener_reverse_monitor.py \
  --out-dir /var/tmp/wiki-shortener-monitor \
  --workers 2 --delay 1
```

The most recent manifest in that directory becomes the next baseline. Use
`--previous PATH` to select a baseline explicitly. Output files use UTC names
such as `2026-09-09T180000Z.json`; exclusive creation makes a timestamp collision
fail instead of overwriting history.

For a scheduler, keep both state and logs outside the checkout. For example, a
daily cron entry can invoke a small local wrapper that changes to a pinned clean
checkout and runs the command above. Do not have cron commit or push. A human
should inspect each manifest, copy only useful sanitized runs into a dated data
directory, and open a normal review PR.

For an offline wiring check, disable both network surfaces:

```bash
python3 scripts/shortener_reverse_monitor.py \
  --out-dir /var/tmp/wiki-shortener-monitor-dry \
  --no-cdx --no-listings
```

## Verification

```bash
python3 -m unittest discover -s scripts -p 'test_shortener_reverse_*.py'
```

Tests cover deterministic event IDs and ordering, all four event classes,
duplicate suppression, sensitive-value redaction, body/error-detail exclusion,
baseline selection and append-only collision refusal. Existing sweep tests cover
bare-code refusal, CDX-only code lookup, no-follow redirect handling, listing
allowlisting and row-local matching.
