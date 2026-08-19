"""
Historique du pipeline LinkedIn — déduplication, analytics, A/B hooks.

Tables SQLite (DB_PATH = $LINKEDIN_DATA_DIR/history.db) :
- posts            : 1 ligne par post publié (slug, topic, format, linkedin_id…)
- hook_variants    : 1 ligne par hook variation générée (winner=1 pour celle choisie)
- post_analytics   : (post_id, metric, count, fetched_at) — métriques LinkedIn best-effort
- format_history   : trace décisions du format_selector pour la rotation
"""

import json
import sqlite3
from datetime import datetime

from config import DB_PATH, MAX_HISTORY_DAYS, SQLITE_TIMEOUT

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        published_at DATETIME NOT NULL,
        topic TEXT NOT NULL,
        slug TEXT NOT NULL,
        format TEXT NOT NULL,
        keywords TEXT NOT NULL,
        linkedin_post_id TEXT,
        linkedin_comment_id TEXT,
        status TEXT NOT NULL DEFAULT 'draft'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_posts_status_date ON posts(status, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug)",
    "CREATE INDEX IF NOT EXISTS idx_posts_format ON posts(format)",
    """CREATE TABLE IF NOT EXISTS hook_variants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        formula TEXT NOT NULL,
        hook TEXT NOT NULL,
        is_winner INTEGER NOT NULL DEFAULT 0,
        judge_reason TEXT,
        FOREIGN KEY (post_id) REFERENCES posts(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_variants_post ON hook_variants(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_variants_winner ON hook_variants(is_winner, formula)",
    """CREATE TABLE IF NOT EXISTS post_analytics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        metric TEXT NOT NULL,
        count INTEGER NOT NULL,
        fetched_at DATETIME NOT NULL,
        FOREIGN KEY (post_id) REFERENCES posts(id),
        UNIQUE(post_id, metric, fetched_at)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_analytics_post ON post_analytics(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_analytics_metric ON post_analytics(metric, fetched_at)",
    """CREATE TABLE IF NOT EXISTS format_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decided_at DATETIME NOT NULL,
        format TEXT NOT NULL,
        reason TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_format_history_date ON format_history(decided_at)",
    # Croissance des followers — granularité journalière depuis l'export XLSX LinkedIn
    """CREATE TABLE IF NOT EXISTS follower_growth (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL,
        new_followers INTEGER NOT NULL,
        total_followers INTEGER,
        imported_at DATETIME NOT NULL,
        UNIQUE(date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_follower_growth_date ON follower_growth(date)",
    # Snapshot démographique audience (job titles, lieux, secteurs)
    """CREATE TABLE IF NOT EXISTS audience_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_at DATETIME NOT NULL,
        dimension TEXT NOT NULL,
        value TEXT NOT NULL,
        percentage REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audience_snapshot ON audience_snapshot(snapshot_at, dimension)",
]


def upsert_follower_growth(date: str, new_followers: int, total_followers: int | None = None) -> None:
    """Insert ou update les followers d'une date donnée (idempotent sur re-import)."""
    init_db()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO follower_growth (date, new_followers, total_followers, imported_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   new_followers = excluded.new_followers,
                   total_followers = COALESCE(excluded.total_followers, follower_growth.total_followers),
                   imported_at = excluded.imported_at""",
            (date, new_followers, total_followers, datetime.now().isoformat()),
        )


def insert_audience_snapshot(snapshot_at: str, rows: list[tuple[str, str, float]]) -> None:
    """rows = liste de (dimension, value, percentage). Wipe pour ce snapshot_at puis insert."""
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM audience_snapshot WHERE snapshot_at = ?", (snapshot_at,))
        conn.executemany(
            "INSERT INTO audience_snapshot (snapshot_at, dimension, value, percentage) VALUES (?, ?, ?, ?)",
            [(snapshot_at, d, v, p) for d, v, p in rows],
        )


def follower_growth_summary(days: int = 30) -> dict:
    """Renvoie un dict {days_covered, total_new_followers, last_known_total, daily_avg}."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT date, new_followers, total_followers FROM follower_growth
               WHERE date > date('now', ? || ' days')
               ORDER BY date DESC""",
            (f"-{days}",),
        ).fetchall()
    if not rows:
        return {"days_covered": 0, "total_new_followers": 0, "last_known_total": None, "daily_avg": 0.0}
    total_new = sum(r[1] for r in rows)
    last_total = next((r[2] for r in rows if r[2] is not None), None)
    return {
        "days_covered": len(rows),
        "total_new_followers": total_new,
        "last_known_total": last_total,
        "daily_avg": round(total_new / len(rows), 2),
    }


def latest_audience_snapshot() -> dict[str, list[tuple[str, float]]]:
    """Renvoie {dimension: [(value, percentage), ...]} pour le snapshot le plus récent."""
    init_db()
    with _conn() as conn:
        last_ts = conn.execute("SELECT MAX(snapshot_at) FROM audience_snapshot").fetchone()[0]
        if not last_ts:
            return {}
        rows = conn.execute(
            "SELECT dimension, value, percentage FROM audience_snapshot WHERE snapshot_at = ? ORDER BY dimension, percentage DESC",
            (last_ts,),
        ).fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for dim, val, pct in rows:
        out.setdefault(dim, []).append((val, pct))
    return out


def _conn():
    return sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT)


# Migrations idempotentes : colonnes ajoutées après le schéma initial.
_MIGRATIONS = [
    "ALTER TABLE posts ADD COLUMN cost_usd REAL",
    "ALTER TABLE posts ADD COLUMN tokens_in INTEGER",
    "ALTER TABLE posts ADD COLUMN tokens_out INTEGER",
    "ALTER TABLE posts ADD COLUMN tokens_cache_write INTEGER",
    "ALTER TABLE posts ADD COLUMN tokens_cache_read INTEGER",
    # ID activity LinkedIn (urn:li:activity:N, visible dans les URLs d'export XLSX).
    # ≠ urn:li:ugcPost/share stocké à la publication — c'est CE mismatch qui empêchait
    # tout rattachement de métriques aux posts du pipeline. Rempli au 1er match par date.
    "ALTER TABLE posts ADD COLUMN linkedin_activity_id TEXT",
    # Registre éditorial du post : pain / pedagogie / preuve (rotation P1)
    "ALTER TABLE posts ADD COLUMN registre TEXT",
]


def init_db():
    with _conn() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        for stmt in _MIGRATIONS:
            try:  # noqa: SIM105
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # colonne déjà présente


# ──────────────────────────────────────────────────────────────
# Posts
# ──────────────────────────────────────────────────────────────
def record_post(
    *,
    topic: str,
    slug: str,
    format: str,
    keywords: list[str],
    linkedin_post_id: str | None = None,
    linkedin_comment_id: str | None = None,
    status: str = "published",
    cost_usd: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_cache_write: int | None = None,
    tokens_cache_read: int | None = None,
    registre: str | None = None,
) -> int:
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO posts
               (published_at, topic, slug, format, keywords, linkedin_post_id, linkedin_comment_id, status,
                cost_usd, tokens_in, tokens_out, tokens_cache_write, tokens_cache_read, registre)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                topic,
                slug,
                format,
                json.dumps(keywords, ensure_ascii=False),
                linkedin_post_id,
                linkedin_comment_id,
                status,
                cost_usd,
                tokens_in,
                tokens_out,
                tokens_cache_write,
                tokens_cache_read,
                registre,
            ),
        )
        return cur.lastrowid


def recent_published_topics(limit: int = 8, days: int = MAX_HISTORY_DAYS) -> list[str]:
    """Sujets des N derniers posts publiés (titre court), du plus récent au plus ancien.

    Alimente le scorer RSS : on les donne à Haiku pour qu'il écarte sémantiquement tout
    article reprenant un sujet ou un angle déjà couvert (remplace l'ancien dédup par mots-clés,
    aveugle au sens : « sanction CNIL IQVIA » vs « sanction CNIL Doctolib » sont le même angle)."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT topic FROM posts
               WHERE status = 'published' AND published_at > datetime('now', ? || ' days')
               ORDER BY published_at DESC LIMIT ?""",
            (f"-{days}", limit),
        ).fetchall()
    return [row[0].split(".", 1)[0].strip()[:100] for row in rows if row[0]]


def get_recent_slugs(days: int = MAX_HISTORY_DAYS) -> set[str]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT slug FROM posts WHERE published_at > datetime('now', ? || ' days') AND status = 'published'",
            (f"-{days}",),
        ).fetchall()
    return {row[0] for row in rows}


def last_published_at() -> datetime | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT published_at FROM posts WHERE status = 'published' ORDER BY published_at DESC LIMIT 1"
        ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def posted_today() -> bool:
    last = last_published_at()
    return last is not None and last.date() == datetime.now().date()


def count_posts_in_days(days: int = 7) -> int:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE published_at > datetime('now', ? || ' days') AND status = 'published'",
            (f"-{days}",),
        ).fetchone()
    return row[0] if row else 0


# ──────────────────────────────────────────────────────────────
# Hook variants (A/B learning)
# ──────────────────────────────────────────────────────────────
def record_hook_variants(post_id: int, variants: list[dict], winner_formula: str, judge_reason: str) -> None:
    init_db()
    with _conn() as conn:
        for v in variants:
            conn.execute(
                "INSERT INTO hook_variants (post_id, formula, hook, is_winner, judge_reason) VALUES (?, ?, ?, ?, ?)",
                (
                    post_id,
                    v["formula"],
                    v["hook"],
                    1 if v["formula"] == winner_formula else 0,
                    judge_reason if v["formula"] == winner_formula else None,
                ),
            )


def recent_winning_hooks(limit: int = 8) -> list[str]:
    """Renvoie les hooks gagnants des N derniers posts publiés, du plus récent au plus ancien.

    Sert à l'anti-répétition d'angle : on injecte ces accroches dans l'Angle Scout pour
    qu'il propose un angle distinct de ce qui est déjà sorti.
    """
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT hv.hook FROM hook_variants hv
               JOIN posts p ON p.id = hv.post_id
               WHERE hv.is_winner = 1 AND p.status = 'published'
               ORDER BY p.published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def formula_win_rate(days: int = 90) -> dict[str, dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT hv.formula, COUNT(*) as picked,
                      COALESCE(AVG(CASE WHEN pa.metric='IMPRESSION'  THEN pa.count END), 0) as avg_impressions,
                      COALESCE(AVG(CASE WHEN pa.metric='INTERACTION' THEN pa.count END), 0) as avg_interactions
               FROM hook_variants hv
               LEFT JOIN posts p ON p.id = hv.post_id
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE hv.is_winner = 1
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY hv.formula""",
            (f"-{days}",),
        ).fetchall()
    return {
        row[0]: {"picked": row[1], "avg_impressions": int(row[2]), "avg_interactions": int(row[3])}
        for row in rows
    }


# ──────────────────────────────────────────────────────────────
# Analytics
# ──────────────────────────────────────────────────────────────
def upsert_analytics(post_id: int, metric: str, count: int, *, monotonic: bool = True) -> bool:
    """Enregistre une métrique. Renvoie True si écrite, False si rejetée.

    monotonic=True (défaut) : les métriques LinkedIn (impressions, interactions) sont
    cumulatives — une valeur INFÉRIEURE au max connu vient forcément d'un export FENÊTRÉ
    (ex : export 7j d'un vieux post → 3 impressions "dans la fenêtre" alors que le cumul
    réel est 1383). On la rejette pour ne pas écraser le cumul affiché par le dashboard.
    """
    init_db()
    with _conn() as conn:
        if monotonic:
            row = conn.execute(
                "SELECT MAX(count) FROM post_analytics WHERE post_id = ? AND metric = ?",
                (post_id, metric),
            ).fetchone()
            if row and row[0] is not None and count < row[0]:
                return False
        conn.execute(
            """INSERT INTO post_analytics (post_id, metric, count, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(post_id, metric, fetched_at) DO UPDATE SET count = excluded.count""",
            (post_id, metric, count, datetime.now().isoformat()),
        )
        return True


def purge_non_monotonic_analytics() -> int:
    """Supprime les lignes analytics héritées des imports fenêtrés : toute ligne dont le
    count est strictement inférieur à un count ANTÉRIEUR pour le même (post, metric).
    Renvoie le nombre de lignes supprimées. Réparation one-shot, idempotente."""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """DELETE FROM post_analytics WHERE id IN (
                   SELECT pa1.id FROM post_analytics pa1
                   WHERE EXISTS (
                       SELECT 1 FROM post_analytics pa2
                       WHERE pa2.post_id = pa1.post_id AND pa2.metric = pa1.metric
                         AND pa2.fetched_at < pa1.fetched_at AND pa2.count > pa1.count
                   )
               )"""
        )
        return cur.rowcount


def set_activity_id(post_id: int, activity_id: str) -> None:
    """Mémorise l'ID activity LinkedIn d'un post (pour matching exact aux imports suivants)."""
    init_db()
    with _conn() as conn:
        conn.execute("UPDATE posts SET linkedin_activity_id = ? WHERE id = ?", (activity_id, post_id))


def find_post_by_activity_id(activity_id: str) -> int | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM posts WHERE linkedin_activity_id = ? LIMIT 1", (activity_id,)
        ).fetchone()
    return row[0] if row else None


def published_count() -> int:
    """Nombre de posts publiés — sert d'index de rotation déterministe (CTA, hashtags,
    mode du 1er commentaire). Stable entre dry-runs, n'avance qu'à la publication."""
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM posts WHERE status = 'published'").fetchone()
    return row[0] if row else 0


def recent_winner_formulas(limit: int = 6) -> list[str]:
    """Formules de hook gagnantes des derniers posts publiés, plus récent d'abord.
    Sert à la rotation honnête des formules (chaque formule doit être exposée
    pour que la comparaison de perfs ait un sens)."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT hv.formula FROM hook_variants hv
               JOIN posts p ON p.id = hv.post_id
               WHERE hv.is_winner = 1 AND p.status = 'published'
               ORDER BY p.published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def recent_registres(limit: int = 10) -> list[str]:
    """Registres des derniers posts publiés, du plus récent au plus ancien.
    Les posts antérieurs à la colonne (registre NULL) sont ignorés."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT registre FROM posts
               WHERE status = 'published' AND registre IS NOT NULL
               ORDER BY published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def find_published_post_by_date(date_iso: str) -> int | None:
    """Post 'published' du jour date_iso (YYYY-MM-DD), ou None.

    Fiable car pipeline.sh garantit 1 publication max/jour (posted_today guard).
    Limite assumée : un post publié MANUELLEMENT le même jour qu'un post pipeline
    serait rattaché au post pipeline — préférer poster manuellement un autre jour.
    """
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM posts WHERE status = 'published' AND date(published_at) = ?",
            (date_iso,),
        ).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def merge_post_into(src_id: int, dst_id: int) -> None:
    """Fusionne un post doublon (src, typiquement 'external') dans le vrai post (dst) :
    réassigne analytics + activity_id, supprime le doublon."""
    init_db()
    with _conn() as conn:
        src = conn.execute(
            "SELECT linkedin_activity_id, linkedin_post_id FROM posts WHERE id = ?", (src_id,)
        ).fetchone()
        if src is None:
            return
        aid = src[0] or (src[1].rsplit(":", 1)[-1] if src[1] and "activity" in src[1] else None)
        conn.execute("UPDATE post_analytics SET post_id = ? WHERE post_id = ?", (dst_id, src_id))
        if aid:
            conn.execute(
                "UPDATE posts SET linkedin_activity_id = ? WHERE id = ? AND linkedin_activity_id IS NULL",
                (aid, dst_id),
            )
        conn.execute("DELETE FROM hook_variants WHERE post_id = ?", (src_id,))
        conn.execute("DELETE FROM posts WHERE id = ?", (src_id,))


def posts_to_fetch_analytics(days: int = 30) -> list[tuple[int, str]]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, linkedin_post_id FROM posts
               WHERE published_at > datetime('now', ? || ' days')
                 AND status = 'published'
                 AND linkedin_post_id IS NOT NULL""",
            (f"-{days}",),
        ).fetchall()
    return rows


def latest_analytics(post_id: int) -> dict[str, int]:
    """Renvoie le dernier count PAR métrique pour un post donné."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT pa1.metric, pa1.count
               FROM post_analytics pa1
               WHERE pa1.post_id = ?
                 AND pa1.fetched_at = (
                    SELECT MAX(pa2.fetched_at)
                    FROM post_analytics pa2
                    WHERE pa2.post_id = pa1.post_id AND pa2.metric = pa1.metric
                 )""",
            (post_id,),
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def posts_in_week(year: int, week: int) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, published_at, topic, slug, format, linkedin_post_id
               FROM posts
               WHERE strftime('%Y', published_at) = ?
                 AND strftime('%W', published_at) = ?
                 AND status = 'published'
               ORDER BY published_at""",
            (str(year), f"{week:02d}"),
        ).fetchall()
    out = []
    for r in rows:
        analytics = latest_analytics(r[0])
        out.append(
            {
                "id": r[0],
                "published_at": r[1],
                "topic": r[2],
                "slug": r[3],
                "format": r[4],
                "linkedin_post_id": r[5],
                "analytics": analytics,
            }
        )
    return out


# ──────────────────────────────────────────────────────────────
# Format history (rotation carousel/text/poll)
# ──────────────────────────────────────────────────────────────
def record_format_decision(format: str, reason: str) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO format_history (decided_at, format, reason) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), format, reason),
        )


def recent_formats(limit: int = 5) -> list[str]:
    """Renvoie les N derniers formats utilisés, du plus récent au plus ancien."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT format FROM posts
               WHERE status = 'published'
               ORDER BY published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]
