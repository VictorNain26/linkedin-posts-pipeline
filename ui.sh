#!/usr/bin/env bash
# ui.sh — lance le dashboard Streamlit en local.
#
# Usage :
#   ./ui.sh          # port 8501 par défaut
#   ./ui.sh 8502     # port custom (utile si 8501 est pris par un tunnel SSH, etc.)
#
# Accès depuis Windows : ssh -L 8501:localhost:8501 victormoi@victorserv
# puis http://localhost:8501 dans le navigateur.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-8501}"

if [ -f "$DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$DIR/.venv/bin/activate"
fi

export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "[ui] Streamlit dashboard → http://localhost:${PORT}"

exec streamlit run "$DIR/dashboard.py" \
    --server.address localhost \
    --server.port "$PORT" \
    --browser.gatherUsageStats false \
    --server.headless true
