"""
Linkedin Pipeline — Pipeline auto-publication LinkedIn pour Victor Lenain.

Façade package qui re-exporte les modules root sans les déplacer physiquement.
Permet `from linkedin_pipeline.config import DB_PATH` aussi bien que `from config import DB_PATH`.

Layout actuel (root layout, pragmatique pour un projet solo) :

    config / log / anthropic_client     ← core
    agents/ (tools.py, system.py)       ← agents Claude (schemas + learnings)
    history / import_analytics_csv      ← data layer
    linkedin_post / linkedin_analytics  ← API LinkedIn
    oauth_setup / token_refresh         ← OAuth
    rss_fetch / format_selector         ← inputs + decision
    generate_post                       ← 8 agents orchestration
    weekly_report                       ← report + analyse IA hebdo → learnings.json
    dashboard / dashboard_queries       ← UI Streamlit + queries DB

Tests : `pytest` depuis la racine — voir tests/conftest.py.
Run : `./pipeline.sh [--dry-run]` ou via Docker `docker compose up -d`.
"""

__version__ = "1.1.0"

# Re-exports principaux pour permettre des imports propres :
#   from linkedin_pipeline import config, history, generate_post

import sys as _sys
from pathlib import Path as _Path

# Permettre l'import des modules root depuis n'importe quel sous-package
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
