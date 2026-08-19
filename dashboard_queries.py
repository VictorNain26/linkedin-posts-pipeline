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
        q = (
            "SELECT id, published_at, topic, slug, format, registre, linkedin_post_id, status, "
            "cost_usd, tokens_in, tokens_out, tokens_cache_write, tokens_cache_read FROM posts"
        )
        params: tuple = ()
        if status:
            q += " WHERE status = ?"
            params = (status,)
        q += " ORDER BY published_at DESC"
        return pd.read_sql_query(q, conn, params=params)


@st.cache_data(ttl=60)
def load_cost_summary() -> dict:
    """Total dépensé + coût moyen par post (tous statuts confondus)."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*), SUM(cost_usd), AVG(cost_usd) FROM posts WHERE cost_usd IS NOT NULL"
        ).fetchone()
    n, total, avg = row if row else (0, None, None)
    return {
        "n_tracked": n or 0,
        "total_usd": total or 0.0,
        "avg_usd": avg or 0.0,
    }


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


# Valeur courante par (post, métrique) : les métriques étant monotones (imports
# fenêtrés rejetés par upsert_analytics), MAX(count) == dernière valeur connue.
# Indispensable pour les moyennes : joindre post_analytics brut ferait peser un post
# avec 5 imports 5x plus lourd qu'un post avec 1 import.
_LATEST_METRICS_CTE = """
    latest AS (
        SELECT post_id,
               MAX(CASE WHEN metric='IMPRESSION'  THEN count END) AS impressions,
               MAX(CASE WHEN metric='INTERACTION' THEN count END) AS interactions
        FROM post_analytics
        GROUP BY post_id
    )
"""


@st.cache_data(ttl=60)
def load_formula_stats(days: int = 90) -> pd.DataFrame:
    """Stats par formule de hook (uniquement winners + posts published).
    Moyennes calculées sur la dernière valeur par post (1 post = 1 poids)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            # S608 : seule interpolation = _LATEST_METRICS_CTE (constante module, pas d'input user)
            f"""WITH {_LATEST_METRICS_CTE}
               SELECT hv.formula,
                      COUNT(*) AS picked_count,
                      COALESCE(AVG(l.impressions), 0) AS avg_impressions,
                      COALESCE(AVG(l.interactions), 0) AS avg_interactions
               FROM hook_variants hv
               JOIN posts p ON p.id = hv.post_id
               LEFT JOIN latest l ON l.post_id = p.id
               WHERE hv.is_winner = 1
                 AND p.status = 'published'
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY hv.formula""",  # noqa: S608
            conn,
            params=(f"-{days}",),
        )


@st.cache_data(ttl=60)
def load_registre_stats(days: int = 90) -> pd.DataFrame:
    """Perfs par registre éditorial (pain/pedagogie/preuve) — alimente la décision
    marketing sur la rotation. Posts antérieurs à la colonne (registre NULL) exclus."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            # S608 : seule interpolation = _LATEST_METRICS_CTE (constante module, pas d'input user)
            f"""WITH {_LATEST_METRICS_CTE}
               SELECT p.registre,
                      COUNT(*) AS n,
                      COALESCE(AVG(l.impressions), 0) AS avg_impressions,
                      COALESCE(AVG(l.interactions), 0) AS avg_interactions
               FROM posts p
               LEFT JOIN latest l ON l.post_id = p.id
               WHERE p.status = 'published'
                 AND p.registre IS NOT NULL
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY p.registre
               ORDER BY n DESC""",  # noqa: S608
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
    """Tableau métriques par post publié sur la fenêtre (dernière valeur par métrique)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT p.id, p.published_at, p.topic, p.format, p.registre,
                      MAX(CASE WHEN pa.metric='IMPRESSION'  THEN pa.count END) AS impressions,
                      MAX(CASE WHEN pa.metric='INTERACTION' THEN pa.count END) AS interactions
               FROM posts p
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE p.status = 'published'
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY p.id, p.published_at, p.topic, p.format, p.registre
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


@st.cache_data(ttl=60)
def load_follower_growth(days: int) -> pd.DataFrame:
    """Croissance abonnés sur la fenêtre glissante (jours)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT date, new_followers, total_followers
               FROM follower_growth WHERE date > date('now', ? || ' days')
               ORDER BY date""",
            conn,
            params=(f"-{days}",),
        )


@st.cache_data(ttl=60)
def load_audience_snapshot() -> tuple[str | None, pd.DataFrame]:
    """Snapshot démographique le plus récent. Retourne (last_ts, DataFrame) ou (None, DataFrame vide)."""
    with sqlite3.connect(DB_PATH) as conn:
        last_ts = conn.execute("SELECT MAX(snapshot_at) FROM audience_snapshot").fetchone()[0]
        if not last_ts:
            return None, pd.DataFrame()
        demo = pd.read_sql_query(
            """SELECT dimension, value, percentage FROM audience_snapshot
               WHERE snapshot_at = ? ORDER BY dimension, percentage DESC""",
            conn,
            params=(last_ts,),
        )
    return last_ts, demo
