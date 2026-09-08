#!/bin/sh
# One read-only tick of swarm-index-watch with persistent state, then a
# committable summary. Designed for cron / launchd (bead jyme).
#
#   WATCHER   checkout of rsavitt/swarm-index-watch, branch usemod-adapter
#             (the darkfibr upstream plus the UseMod/ProWiki/Oddmuse adapter,
#             darkfibr/swarm-index-watch#1)          default ~/swarm-index-watch
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

mkdir -p "$STATE/shards"
START=$(date -u +%Y-%m-%dT%H:%MZ)
DAY=$(date -u +%Y-%m-%d)
SHARD=$STATE/shards/$DAY.jsonl
BEFORE=0
[ -f "$SHARD" ] && BEFORE=$(wc -l < "$SHARD" | tr -d ' ')
python3 "$WATCHER/swarm_index_watch.py" --config "$CONFIG" --dir "$STATE"
python3 "$REPO/scripts/swarm_index_watch_summarize.py" "$SHARD" --from-line "$BEFORE" \
  --run-utc "$START" --out "$REPO/data/swarm_index_watch_tick_$DAY.json"
