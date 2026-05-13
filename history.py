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
]


def _conn():
    return sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT)


def init_db():
    with _conn() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)


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
) -> int:
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO posts
               (published_at, topic, slug, format, keywords, linkedin_post_id, linkedin_comment_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                topic,
                slug,
                format,
                json.dumps(keywords, ensure_ascii=False),
                linkedin_post_id,
                linkedin_comment_id,
                status,
            ),
        )
        return cur.lastrowid


def get_recent_keywords(days: int = MAX_HISTORY_DAYS) -> list[str]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT keywords FROM posts WHERE published_at > datetime('now', ? || ' days') AND status = 'published'",
            (f"-{days}",),
        ).fetchall()
    all_kw: list[str] = []
    for row in rows:
        all_kw.extend(json.loads(row[0]))
    return all_kw


def get_recent_slugs(days: int = MAX_HISTORY_DAYS) -> set[str]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT slug FROM posts WHERE published_at > datetime('now', ? || ' days') AND status = 'published'",
            (f"-{days}",),
        ).fetchall()
    return {row[0] for row in rows}


def keyword_overlap_ratio(new_keywords: list[str], days: int = MAX_HISTORY_DAYS) -> float:
    new_set = {k.lower() for k in new_keywords}
    if not new_set:
        return 0.0
    recent = {k.lower() for k in get_recent_keywords(days)}
    if not recent:
        return 0.0
    return len(new_set & recent) / len(new_set)


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


def formula_win_rate(days: int = 90) -> dict[str, dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT hv.formula, COUNT(*) as picked,
                      COALESCE(AVG(CASE WHEN pa.metric='IMPRESSION' THEN pa.count END), 0) as avg_impressions
               FROM hook_variants hv
               LEFT JOIN posts p ON p.id = hv.post_id
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE hv.is_winner = 1
                 AND p.published_at > datetime('now', ? || ' days')
               GROUP BY hv.formula""",
            (f"-{days}",),
        ).fetchall()
    return {row[0]: {"picked": row[1], "avg_impressions": int(row[2])} for row in rows}


# ──────────────────────────────────────────────────────────────
# Analytics
# ──────────────────────────────────────────────────────────────
def upsert_analytics(post_id: int, metric: str, count: int) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO post_analytics (post_id, metric, count, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(post_id, metric, fetched_at) DO UPDATE SET count = excluded.count""",
            (post_id, metric, count, datetime.now().isoformat()),
        )


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
