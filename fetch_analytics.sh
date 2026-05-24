#!/usr/bin/env bash
# fetch_analytics.sh — fetch analytics via API LinkedIn (si scope disponible).
#
# ⚠️ Avec le produit "Share on LinkedIn" seul (scope w_member_social),
#    l'API analytics est inaccessible (exit 2 → scope_missing).
#    Dans ce cas : utilise import_analytics_csv.py avec l'export UI hebdo.
#
# Si tu décroches Community Management API plus tard, ce script reprend
# le relais automatiquement (le scope r_member_postAnalytics suffit).

set -uo pipefail
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
set +e
python3 "$DIR/linkedin_analytics.py" 2>>"$LOG_FILE"
RC=$?
set -e

case "$RC" in
    0)
        log "=== Analytics fetch done ==="
        ;;
    2)
        log "SKIP : scope r_member_postAnalytics absent (Community Management API)."
        log "  → utilise : python3 $DIR/import_analytics_csv.py /chemin/vers/export.csv"
        # Exit 0 pour ne pas spammer le healthcheck d'alertes : c'est un "pas dispo", pas un échec.
        exit 0
        ;;
    *)
        log "ERROR : linkedin_analytics.py exit $RC"
        exit "$RC"
        ;;
esac
