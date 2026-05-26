#!/usr/bin/env bash
# cleanup.sh — purge des fichiers transients pour économiser l'espace.
#
# Quoi garder :
# - history.db                 → permanent (essentiel : dédup + analytics + hook learning)
# - backups/                   → géré par backup.sh (rotation 14j)
#
# Quoi purger :
# - output/YYYY-MM-DD-slug/    → >7 jours (le post est déjà sur LinkedIn, history.db a l'essentiel)
# - logs/*.log                 → >14 jours
# - logs/metrics.jsonl         → compress quand >5MB
#
# Cron suggéré : daily 03h00.

set -euo pipefail
IFS=$'\n\t'

DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
OUTPUT_DIR="$DATA_DIR/output"
LOG_DIR="$DATA_DIR/logs"

OUTPUT_RETENTION_DAYS="${LINKEDIN_OUTPUT_RETENTION_DAYS:-7}"
LOG_RETENTION_DAYS="${LINKEDIN_LOG_RETENTION_DAYS:-14}"
METRICS_MAX_BYTES="${LINKEDIN_METRICS_MAX_BYTES:-5242880}"  # 5 MB

CLEANUP_LOG="$LOG_DIR/cleanup.log"
mkdir -p "$LOG_DIR"

log() { echo "[$(date --iso-8601=seconds)] $*" | tee -a "$CLEANUP_LOG"; }

log "=== Cleanup start ==="

# ── 1. Purge output/ : dossiers de posts publiés >7 jours ──
if [ -d "$OUTPUT_DIR" ]; then
    PURGED_OUTPUTS=0
    while IFS= read -r -d '' dir; do
        rm -rf "$dir"
        PURGED_OUTPUTS=$((PURGED_OUTPUTS + 1))
    done < <(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$OUTPUT_RETENTION_DAYS" -print0 2>/dev/null)
    log "Outputs purged (>${OUTPUT_RETENTION_DAYS}d): $PURGED_OUTPUTS"
fi

# ── 2. Purge logs/ : fichiers .log >14 jours ──
if [ -d "$LOG_DIR" ]; then
    PURGED_LOGS=$(find "$LOG_DIR" -maxdepth 1 -name "*.log" -mtime +"$LOG_RETENTION_DAYS" -print -delete 2>/dev/null | wc -l)
    log "Log files purged (>${LOG_RETENTION_DAYS}d): $PURGED_LOGS"
fi

# ── 3. Compression metrics.jsonl si >5MB ──
METRICS_FILE="$LOG_DIR/metrics.jsonl"
if [ -f "$METRICS_FILE" ]; then
    SIZE=$(stat -c%s "$METRICS_FILE" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt "$METRICS_MAX_BYTES" ]; then
        ARCHIVE="$LOG_DIR/metrics-$(date +%Y%m%d).jsonl.gz"
        gzip -c "$METRICS_FILE" > "$ARCHIVE"
        : > "$METRICS_FILE"  # truncate (pas de gap pour les apps qui appendent)
        log "metrics.jsonl rotated → $ARCHIVE ($SIZE bytes)"
    fi
fi

# ── 4. Affiche l'espace utilisé total ──
TOTAL_SIZE=$(du -sh "$DATA_DIR" 2>/dev/null | awk '{print $1}')
log "Total data dir size: $TOTAL_SIZE"

log "=== Cleanup done ==="
