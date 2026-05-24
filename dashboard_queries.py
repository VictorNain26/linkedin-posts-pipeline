"""
Helpers data pour le dashboard Streamlit : queries SQLite + filesystem.

Tout est cached via @st.cache_data (TTL 60s pour DB, 300s pour PDF render).
Extrait de dashboard.py pour découpler les data access de la UI.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pypdfium2 as pdfium
import streamlit as st

from config import DB_PATH, OUTPUT_DIR


# ──────────────────────────────────────────────────────────────
# DB queries — TTL 60s, suffisant pour un dashboard solo
# ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_posts(status: str | None = None) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        q = "SELECT id, published_at, topic, slug, format, linkedin_post_id, status FROM posts"
        params: tuple = ()
        if status:
            q += " WHERE status = ?"
            params = (status,)
        q += " ORDER BY published_at DESC"
        return pd.read_sql_query(q, conn, params=params)


@st.cache_data(ttl=60)
def load_hook_variants(post_id: int) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT formula, hook, is_winner, judge_reason FROM hook_variants "
            "WHERE post_id = ? ORDER BY is_winner DESC",
            conn,
            params=(post_id,),
        )


@st.cache_data(ttl=60)
def load_latest_analytics(post_id: int) -> pd.DataFrame:
    """Dernière valeur connue par métrique (post_analytics garde l'historique)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT pa1.metric, pa1.count, pa1.fetched_at
               FROM post_analytics pa1
               WHERE pa1.post_id = ?
                 AND pa1.fetched_at = (
                    SELECT MAX(pa2.fetched_at) FROM post_analytics pa2
                    WHERE pa2.post_id = pa1.post_id AND pa2.metric = pa1.metric
                 )
               ORDER BY pa1.metric""",
            conn,
            params=(post_id,),
        )


@st.cache_data(ttl=60)
def load_formula_stats(days: int = 90) -> pd.DataFrame:
    """Stats par formule de hook (uniquement winners + posts published)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT hv.formula,
                      COUNT(*) AS picked_count,
                      COALESCE(AVG(CASE WHEN pa.metric='IMPRESSION' THEN pa.count END), 0) AS avg_impressions,
                      COALESCE(AVG(CASE WHEN pa.metric='REACTION' THEN pa.count END), 0) AS avg_reactions,
                      COALESCE(AVG(CASE WHEN pa.metric='COMMENT' THEN pa.count END), 0) AS avg_comments
               FROM hook_variants hv
               LEFT JOIN posts p ON p.id = hv.post_id
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE hv.is_winner = 1
                 AND p.status = 'published'
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY hv.formula""",
            conn,
            params=(f"-{days}",),
        )


@st.cache_data(ttl=60)
def load_format_distribution(days: int = 90) -> pd.DataFrame:
    """Distribution des formats utilisés (carousel/text) sur la fenêtre."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT format, COUNT(*) AS n
               FROM posts
               WHERE status = 'published'
                 AND published_at > datetime('now', ? || ' days')
               GROUP BY format""",
            conn,
            params=(f"-{days}",),
        )


@st.cache_data(ttl=60)
def load_post_metrics_summary(days: int = 90) -> pd.DataFrame:
    """Tableau métriques par post (published + external) sur la fenêtre."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT p.id, p.published_at, p.topic, p.format,
                      MAX(CASE WHEN pa.metric='IMPRESSION' THEN pa.count END) AS impressions,
                      MAX(CASE WHEN pa.metric='REACTION' THEN pa.count END) AS reactions,
                      MAX(CASE WHEN pa.metric='COMMENT' THEN pa.count END) AS comments,
                      MAX(CASE WHEN pa.metric='RESHARE' THEN pa.count END) AS reshares,
                      MAX(CASE WHEN pa.metric='POST_SAVE' THEN pa.count END) AS saves
               FROM posts p
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE p.status = 'published'
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY p.id, p.published_at, p.topic, p.format
               ORDER BY p.published_at DESC""",
            conn,
            params=(f"-{days}",),
        )


# ──────────────────────────────────────────────────────────────
# Filesystem helpers (post output dir + PDF render)
# ──────────────────────────────────────────────────────────────
def post_dir_for(published_at: str, slug: str) -> Path | None:
    """Retourne le dossier output d'un post (s'il existe encore — cleanup.sh peut l'avoir purgé)."""
    try:
        date_tag = datetime.fromisoformat(published_at).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    candidate = Path(OUTPUT_DIR) / f"{date_tag}-{slug}"
    return candidate if candidate.exists() else None


@st.cache_data(ttl=300)
def render_pdf_pages(pdf_path_str: str, scale: float = 1.5) -> list:
    """Render chaque page PDF en PIL Image, cachées 5min pour éviter re-rendering à chaque rerun."""
    pdf = pdfium.PdfDocument(pdf_path_str)
    images = []
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=scale)
        images.append(bitmap.to_pil())
    return images


def read_text_file(path: Path) -> str:
    """Lecture safe d'un fichier texte (retourne '' si absent/illisible)."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""
