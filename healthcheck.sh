#!/usr/bin/env bash
# healthcheck.sh — daily cron 08h00
# Vérifie : token LinkedIn proche expiration, pipeline activity, RSS health.
# Écrit dans logs/healthcheck.log + alerts.log si problèmes.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
LOG_DIR="$DATA_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/healthcheck.log"
ALERTS_FILE="$LOG_DIR/alerts.log"

if [ -f "$DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$DIR/.venv/bin/activate"
fi

export LINKEDIN_DATA_DIR="$DATA_DIR"
export PIPELINE_DIR="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

ALERTS=()

log() { echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG_FILE"; }
alert() {
    ALERTS+=("$1")
    echo "[$(date --iso-8601=seconds)] ALERT: $1" | tee -a "$ALERTS_FILE" >&2
}

log "=== Healthcheck start ==="

# ── 1. Token LinkedIn — refresh préventif si < 14 jours ─────
# (impossible de lire l'expiration depuis le token, on déclenche un refresh prophylactique)
LAST_REFRESH_FILE="$DATA_DIR/.last_token_refresh"
NOW=$(date +%s)
REFRESH_NEEDED="false"

if [ -f "$LAST_REFRESH_FILE" ]; then
    LAST=$(cat "$LAST_REFRESH_FILE")
    AGE=$(( (NOW - LAST) / 86400 ))
    log "Token refreshed $AGE day(s) ago"
    if [ "$AGE" -ge 50 ]; then
        REFRESH_NEEDED="true"
    fi
else
    log "No refresh marker found — refreshing"
    REFRESH_NEEDED="true"
fi

if [ "$REFRESH_NEEDED" = "true" ]; then
    log "Refreshing LinkedIn access_token…"
    if python3 "$DIR/token_refresh.py" 2>>"$LOG_FILE"; then
        echo "$NOW" > "$LAST_REFRESH_FILE"
        log "Token refresh OK"
    else
        alert "LinkedIn token refresh FAILED — manual OAuth re-run may be required"
    fi
fi

# ── 2. Pipeline activity — au moins 1 post / 5 derniers jours ──
RECENT_COUNT=$(python3 -c "from history import count_posts_in_days; print(count_posts_in_days(5))" 2>>"$LOG_FILE" || echo "0")
log "Posts in last 5 days: $RECENT_COUNT"
if [ "$RECENT_COUNT" = "0" ]; then
    alert "No posts published in last 5 days — pipeline may be stuck"
fi

# ── 3. Last pipeline run — failed N times ? ─────────────────
if [ -f "$LOG_DIR/pipeline.log" ]; then
    RECENT_ERRORS=$(tail -200 "$LOG_DIR/pipeline.log" | grep -c "ERROR" || true)
    if [ "$RECENT_ERRORS" -ge 2 ]; then
        alert "Pipeline log shows $RECENT_ERRORS ERROR entries in last 200 lines"
    fi
fi

# ── 3b. Analytics fetch fresh ? (au moins 1 fetch dans les 36h) ──
if [ -f "$LOG_DIR/analytics.log" ]; then
    LAST_ANALYTICS=$(stat -c%Y "$LOG_DIR/analytics.log")
    AGE_HOURS=$(( (NOW - LAST_ANALYTICS) / 3600 ))
    if [ "$AGE_HOURS" -gt 36 ]; then
        alert "Analytics not fetched in last $AGE_HOURS h — cron stuck or token expired"
    fi
else
    log "No analytics.log yet (script may not have run yet)"
fi

# ── 4. RSS sources — alert if >50% failing ─────────────────
log "Checking RSS sources reachability…"
RSS_FAILURES=$(python3 - <<'PY' 2>>"$LOG_FILE"
import socket, sys
from config import RSS_SOURCES, RSS_FETCH_TIMEOUT
import feedparser
socket.setdefaulttimeout(RSS_FETCH_TIMEOUT)
fail = 0
for url in RSS_SOURCES:
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            fail += 1
    except Exception:
        fail += 1
print(fail)
PY
)
TOTAL_RSS=$(python3 -c "from config import RSS_SOURCES; print(len(RSS_SOURCES))" 2>>"$LOG_FILE")
log "RSS health: $RSS_FAILURES/$TOTAL_RSS sources failing"
if [ "$RSS_FAILURES" -gt $(( TOTAL_RSS / 2 )) ]; then
    alert "More than half of RSS sources are unreachable ($RSS_FAILURES/$TOTAL_RSS)"
fi

# ── 5. Disk space (data dir) ────────────────────────────────
AVAILABLE_MB=$(df -m "$DATA_DIR" | awk 'NR==2 {print $4}')
log "Free disk on data dir: ${AVAILABLE_MB}MB"
if [ "$AVAILABLE_MB" -lt 500 ]; then
    alert "Low disk space on data dir: ${AVAILABLE_MB}MB remaining"
fi

# ── Résumé ─────────────────────────────────────────────────
if [ "${#ALERTS[@]}" -eq 0 ]; then
    log "=== Healthcheck OK ==="
    exit 0
else
    log "=== Healthcheck DONE with ${#ALERTS[@]} alert(s) ==="
    for a in "${ALERTS[@]}"; do
        log "  - $a"
    done
    exit 1
fi
