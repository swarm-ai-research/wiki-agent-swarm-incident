#!/bin/sh
# One read-only tick of swarm-index-watch with persistent state, then a
# committable summary. Designed for cron / launchd (bead jyme).
#
#   WATCHER   checkout of rsavitt/swarm-index-watch, branch tick/on-upstream-main
#             (darkfibr upstream f9f82c7 -- the live watch net, ip_watchlist and
#             Benford score -- plus our UseMod/ProWiki/Oddmuse adapter and the
#             item-timestamp clock fix, darkfibr/swarm-index-watch#1)
#                                                    default ~/swarm-index-watch
#   STATE     state.json + shards/, outside any repo  default ~/.local/state/swarm-index-watch
#   REPO      this repository                         default: the script's parent
#
# The state dir is what makes ticks incremental: the first tick over a venue
# reads its whole listing window, every later tick reports only items whose
# id was not in state.json. Losing the state dir turns the next tick back into
# a 30-day catch-up, so keep it out of scratch space.
#
# Read-only, always: this refuses to run if any venue turns fetch_body on, and
# the venue config never names a write path (public-board.com is watched at
# /threads; /?post= is never requested). Cron example, 06:10 UTC daily:
#   10 6 * * * /Users/you/wiki-agent-swarm-incident/scripts/swarm_index_watch_tick.sh >> ~/.local/state/swarm-index-watch/cron.log 2>&1
# Commit the summary it writes under data/ when it says something worth keeping.
set -eu

REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
WATCHER=${WATCHER:-$HOME/swarm-index-watch}
STATE=${STATE:-$HOME/.local/state/swarm-index-watch}
CONFIG=$REPO/data/swarm_index_watch_venues.json

python3 - "$CONFIG" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1]))
bad = [v["name"] for v in cfg["venues"] if v.get("fetch_body", True)]
if bad:
    sys.exit("refusing to run: fetch_body is on for %s (this tick is read-only)" % ", ".join(bad))
urls = [str(v.get(k, "")) for v in cfg["venues"] for k in ("url", "api", "item_url", "page_base")]
if any("post=" in u for u in urls):
    sys.exit("refusing to run: a venue URL names a write path")
EOF

# The watcher must measure cadence on each item's own timestamp, not on fetch
# time. Upstream main stores time.time() in the author-hit window, which feeds
# both cadence and the Benford interval score; on a 15-min cron that quantises
# every interval to a multiple of 900s and fires on everything (a lognormal
# human scores chi2 494 against a fire threshold of 25). Our branch stores
# item.get("ts"). Check the checkout rather than trusting a branch name.
grep -q 'append(item.get("ts") or time.time())' "$WATCHER/swarm_index_watch.py" || {
  echo "refusing to run: $WATCHER does not carry the item-timestamp clock fix" >&2
  echo "  (expected branch tick/on-upstream-main; cadence and benford would score fetch times)" >&2
  exit 1
}

mkdir -p "$STATE/shards"
START=$(date -u +%Y-%m-%dT%H:%MZ)
DAY=$(date -u +%Y-%m-%d)
SHARD=$STATE/shards/$DAY.jsonl
BEFORE=0
[ -f "$SHARD" ] && BEFORE=$(wc -l < "$SHARD" | tr -d ' ')
python3 "$WATCHER/swarm_index_watch.py" --config "$CONFIG" --dir "$STATE"
python3 "$REPO/scripts/swarm_index_watch_summarize.py" "$SHARD" --from-line "$BEFORE" \
  --run-utc "$START" --out "$REPO/data/swarm_index_watch_tick_$DAY.json"
