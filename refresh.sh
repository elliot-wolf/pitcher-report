#!/bin/bash
# refresh.sh — rebuild the scouting card from live Statcast + StatsAPI.
# Driven by launchd (see com.elliot.pitcher-report.plist); safe to run by hand.

set -uo pipefail
# Resolve our own location so a clone works anywhere, on anyone's machine.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-$(command -v python3)}"
[ -x "$PY" ] || { echo "python3 not found on PATH"; exit 1; }
LOG="$DIR/refresh.log"
LOCK="$DIR/.refresh.lock.d"
SEASON="${SEASON:-2026}"
LIMIT="${LIMIT:-60}"

cd "$DIR" || exit 1
# mkdir is atomic on macOS; flock is not available here.
if ! mkdir "$LOCK" 2>/dev/null; then
  # stale lock (>2h) from a killed run? take it over.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    echo "$(date '+%F %T')  another refresh is running — skipping" >>"$LOG"; exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

say() { echo "$(date '+%F %T')  $*" >>"$LOG"; }

# keep the log to the last ~800 lines
if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 800 ]; then
  tail -n 400 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

say "── refresh start (season $SEASON, limit $LIMIT)"

# Build to a temp file so a failed/partial fetch never clobbers a good card.
TMP="$DIR/.cards.new.json"
if ! "$PY" build_cards.py --season "$SEASON" --limit "$LIMIT" --probable-days 2 \
        --out "$TMP" >>"$LOG" 2>&1; then
  say "!! build_cards.py failed — keeping yesterday's card"
  rm -f "$TMP"; exit 1
fi

# Sanity gate: must be real JSON with a plausible number of pitchers.
if ! "$PY" - "$TMP" <<'PYCHK' >>"$LOG" 2>&1
import json, sys
d = json.load(open(sys.argv[1]))
n = len(d.get("pitchers", []))
assert n >= 30, f"only {n} pitchers in payload"
assert d.get("baseline", {}).get("R", {}).get("n", 0) > 20000, "baseline too thin"
print(f"   sanity ok: {n} pitchers, baseline {d['baseline']['R']['n']+d['baseline']['L']['n']:,} pitches")
PYCHK
then
  say "!! payload failed sanity check — keeping yesterday's card"
  rm -f "$TMP"; exit 1
fi

mv "$TMP" cards.json
if ! "$PY" bake.py >>"$LOG" 2>&1; then
  say "!! bake.py failed"
  exit 1
fi

say "✓ refreshed — $(ls -lh pitcher-card.html | awk '{print $5}') · $(date '+%F %T')"
date '+%F %T' > "$DIR/.last-refresh"
