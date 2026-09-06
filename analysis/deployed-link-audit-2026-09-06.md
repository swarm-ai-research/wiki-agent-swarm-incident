# Deployed incident DB link audit — 2026-09-06

Audited `https://swarm.termina.digital/db/` and its internal links. All 444
internal targets returned HTTP 200 and all 1,279 checked internal fragment
anchors resolved. The following external targets are stale or unavailable:

| Current target | Page | Disposition |
| --- | --- | --- |
| `github.com/AI-Safety-Commons/rlvr-collusion-incident-wiki/blob/main/articles/timing-and-termination.md` | `evidence.html` | Replace `main` with `master`; the article exists at `https://github.com/AI-Safety-Commons/rlvr-collusion-incident-wiki/blob/master/articles/timing-and-termination.md`. |
| `github.com/AI-Safety-Commons/swarmchaser/blob/main/LICENSE` | `about.html` | Repository is gone. Replace with the live CC0 license file at `https://github.com/AI-Safety-Commons/rlvr-collusion-incident-wiki/blob/master/LICENSE.md`, or remove the repository-specific wording. |
| `/pub/datasets/manifest.json` | `about.html` | 404. Remove the curated-bundle link/sentence or publish the missing manifest before restoring it. |
| `paste.linuxiarz.pl/lists/570` | `venue/paste-linuxiarz.html` | 404. Keep the venue record, but mark this list as unavailable rather than linking to a guessed replacement. |
| `nopaste.ceske-hry.cz/` | `venue/paste-ceske-hry.html` | 404. Keep the venue record, but mark the endpoint as unavailable. |
| `yourls.biz/steg17796785680+` | `evidence.html` | Host no longer resolves and the destination cannot be recovered from the live page. Remove the dead shortlink or retain it only as an unlinked historical indicator. |

Operationally unavailable endpoints should not be treated as confirmed dead:
`ctxr.me` (502), `linkrutgon.net` (timeout), `paste.flashrom.org` (TLS
failure), and `paste.linuxiarz.pl/api/recent` (403). Recheck these from a
different network before changing their records.

