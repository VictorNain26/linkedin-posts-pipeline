#!/usr/bin/env bash
# weekly_report.sh — cron lundi 07h
# Génère le rapport markdown de la semaine N-1 + envoie par email.

set -euo pipefail
IFS=$'\n\t'

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
LOG_DIR="$DATA_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/weekly_report.log"

if [ -f "$DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$DIR/.venv/bin/activate"
fi

export LINKEDIN_DATA_DIR="$DATA_DIR"
export PIPELINE_DIR="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

log() { echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG_FILE"; }

log "=== Weekly report start ==="
REPORT_PATH=$(python3 "$DIR/weekly_report.py" 2>>"$LOG_FILE")
log "Report generated: $REPORT_PATH"
log "=== Weekly report done ==="
