#!/usr/bin/env bash
# backup.sh — backup automatique du volume de data.
#
# Strategy : SQLite dump + tar.gz du data dir → garder N versions rotatives.
# Cron suggéré : daily 02h00 (heure creuse).
#
# Restore : tar xzf backup-YYYY-MM-DD.tar.gz -C /

set -euo pipefail
IFS=$'\n\t'

DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
BACKUP_DIR="${LINKEDIN_BACKUP_DIR:-$HOME/linkedin-posts-backups}"
RETENTION_DAYS="${LINKEDIN_BACKUP_RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

LOG_FILE="$DATA_DIR/logs/backup.log"
log() { echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG_FILE"; }

log "=== Backup start ==="

# 1. Dump SQLite proprement (capture transactions cohérentes)
DB_PATH="$DATA_DIR/history.db"
DUMP_FILE="$BACKUP_DIR/history-$(date +%Y-%m-%d).sql"

if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" ".backup '$DUMP_FILE.bin'"
    sqlite3 "$DUMP_FILE.bin" ".dump" > "$DUMP_FILE"
    rm "$DUMP_FILE.bin"
    log "SQLite dump: $DUMP_FILE ($(wc -l < "$DUMP_FILE") lines)"
else
    log "WARN: $DB_PATH not found, skipping SQLite dump"
fi

# 2. Archive complète du data dir (sauf logs volatils)
ARCHIVE="$BACKUP_DIR/linkedin-data-$(date +%Y-%m-%d-%H%M).tar.gz"
tar czf "$ARCHIVE" \
    --exclude="$DATA_DIR/logs/*.log" \
    --exclude="$DATA_DIR/output/.tmp*" \
    --exclude="$DATA_DIR/.pipeline.lock" \
    --exclude="$DATA_DIR/.analytics.lock" \
    "$DATA_DIR" 2>>"$LOG_FILE" || true

SIZE=$(stat -c%s "$ARCHIVE" 2>/dev/null || echo 0)
log "Archive: $ARCHIVE (${SIZE} bytes)"

# 3. Rotation : supprimer backups > RETENTION_DAYS
find "$BACKUP_DIR" -name "linkedin-data-*.tar.gz" -mtime +"$RETENTION_DAYS" -delete 2>>"$LOG_FILE" || true
find "$BACKUP_DIR" -name "history-*.sql" -mtime +"$RETENTION_DAYS" -delete 2>>"$LOG_FILE" || true

REMAINING=$(find "$BACKUP_DIR" -name "linkedin-data-*.tar.gz" | wc -l)
log "Retention: kept $REMAINING archive(s), deleted >$RETENTION_DAYS days old"

# 4. Optionnel : sync vers stockage distant (commenté par défaut)
# Décommenter si tu utilises rclone (Backblaze B2, S3, etc.)
# Pré-requis : `apt install rclone && rclone config`
#
# if command -v rclone &>/dev/null && [ -n "${RCLONE_REMOTE:-}" ]; then
#     rclone copy "$ARCHIVE" "$RCLONE_REMOTE:linkedin-backups/" --quiet 2>>"$LOG_FILE"
#     log "Remote sync: $RCLONE_REMOTE:linkedin-backups/"
# fi

log "=== Backup done ==="
