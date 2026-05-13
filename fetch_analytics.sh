#!/usr/bin/env bash
# fetch_analytics.sh — cron daily 21h
# Récupère les métriques LinkedIn pour les posts publiés des 30 derniers jours.

set -euo pipefail
IFS=$'\n\t'

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
LOG_DIR="$DATA_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/analytics.log"

if [ -f "$DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$DIR/.venv/bin/activate"
fi

export LINKEDIN_DATA_DIR="$DATA_DIR"
export PIPELINE_DIR="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

log() { echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG_FILE"; }

# Lock pour éviter 2 runs simultanés (cron + manuel)
LOCK_FILE="$DATA_DIR/.analytics.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "another analytics run in progress, exiting"
    exit 0
fi

log "=== Analytics fetch start ==="
python3 "$DIR/linkedin_analytics.py" 2>>"$LOG_FILE"
log "=== Analytics fetch done ==="
