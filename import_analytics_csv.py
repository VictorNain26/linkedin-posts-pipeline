"""
Importer des analytics LinkedIn depuis l'export XLSX français de l'UI (2026).

L'API LinkedIn `memberCreatorPostAnalytics` requiert `r_member_postAnalytics` (Community
Management API, gated entité légale + business email). Inaccessible en perso → on passe
par l'export XLSX manuel hebdo depuis https://www.linkedin.com/analytics/creator.

Format XLSX LinkedIn FR 2026 — 5 feuilles :
  - DÉCOUVERTE           : KPIs globaux (Impressions, Membres touchés) sur la période
  - ENGAGEMENT           : impressions+interactions par jour (granularité globale)
  - MEILLEURS POSTS      : par post — 2 tableaux côte-à-côte (URL+Date+Interactions,
                            URL+Date+Impressions), header sur row 2
  - ABONNÉS              : nouveaux abonnés/jour + total à la date d'export
  - DONNÉES DÉMOGRAPHIQUES : audience breakdown (Intitulés de poste, Lieux, Secteurs, etc.)

Workflow :
  1. https://www.linkedin.com/analytics/creator → Export XLSX
  2. python3 import_analytics_csv.py /chemin/vers/export.xlsx
     (ou drag-drop dans le dashboard Streamlit → page Analytics)

Ce que fait l'import :
  - MEILLEURS POSTS → post_analytics (IMPRESSION, INTERACTION). Si le post n'existe pas
    en DB (= post manuel publié hors pipeline), on le crée avec status='external'.
  - ABONNÉS → follower_growth (par jour, idempotent)
  - DONNÉES DÉMOGRAPHIQUES → audience_snapshot (wipe + insert pour le snapshot date)
  - DÉCOUVERTE et ENGAGEMENT : skip (déjà couvert par post_analytics agrégés)

Sortie : dict {posts_matched, posts_external_created, metrics_written,
                follower_days_imported, demo_rows_imported}.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from history import (
    _conn,
    find_post_by_activity_id,
    find_published_post_by_date,
    init_db,
    insert_audience_snapshot,
    merge_post_into,
    purge_non_monotonic_analytics,
    set_activity_id,
    upsert_analytics,
    upsert_follower_growth,
)

# Regex pour extraire l'ID activity depuis une URL LinkedIn type
# https://www.linkedin.com/feed/update/urn:li:activity:7234567890123456789/
URL_ID_RE = re.compile(r"(?:activity|ugcPost|share)[-:](\d{10,})")

# Noms de feuille attendus (FR + EN fallback)
SHEET_POSTS = ["MEILLEURS POSTS", "TOP POSTS", "BEST POSTS"]
SHEET_FOLLOWERS = ["ABONNÉS", "ABONNES", "FOLLOWERS"]
SHEET_DEMO = ["DONNÉES DÉMOGRAPHIQUES", "DEMOGRAPHICS", "AUDIENCE"]


# ──────────────────────────────────────────────────────────────
# Helpers parsing
# ──────────────────────────────────────────────────────────────
def _find_sheet(xls, candidates: list[str]) -> str | None:
    for sheet in xls.sheet_names:
        norm = sheet.strip().upper()
        for c in candidates:
            if c.upper() in norm:
                return sheet
    return None


def _parse_french_date(raw) -> str | None:
    """Convertit '24/05/2026' ou datetime en ISO date 'YYYY-MM-DD'."""
    if raw is None or (hasattr(raw, "__class__") and raw.__class__.__name__ == "NaTType"):
        return None
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d")
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_activity_id(url) -> str | None:
    if not url or str(url).lower() in ("nan", "none"):
        return None
    m = URL_ID_RE.search(str(url))
    return m.group(1) if m else None


def _safe_int(v) -> int | None:
    try:
        if v is None:
            return None
        s = str(v).strip().replace(" ", "").replace(",", ".")
        if not s or s.lower() in ("nan", "none"):
            return None
        return int(float(s))
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────
# Parsers par feuille
# ──────────────────────────────────────────────────────────────
def _parse_top_posts_sheet(xlsx_path: Path, sheet_name: str) -> list[dict]:
    """Renvoie une liste de dicts {url, activity_id, date, impressions, interactions}.

    Format : 2 tableaux côte-à-côte (col 0-2 = Interactions, col 4-6 = Impressions).
    Header sur row 2 (rows 0-1 = commentaire LinkedIn + vide). On merge sur URL.
    """
    import pandas as pd  # noqa: PLC0415

    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine="openpyxl", header=None, skiprows=3)
    if df.empty:
        return []

    # Construction d'un dict {url: post_data}
    by_url: dict[str, dict] = {}

    # Tableau 1 : col 0=URL, 1=Date, 2=Interactions
    for _, row in df.iterrows():
        url_raw = row.iloc[0] if len(row) > 0 else None
        url = str(url_raw).strip() if url_raw is not None and str(url_raw).lower() != "nan" else None
        if not url:
            continue
        aid = _extract_activity_id(url)
        if not aid:
            continue
        by_url[aid] = {
            "url": url,
            "activity_id": aid,
            "date": _parse_french_date(row.iloc[1] if len(row) > 1 else None),
            "interactions": _safe_int(row.iloc[2] if len(row) > 2 else None),
            "impressions": None,
        }

    # Tableau 2 : col 4=URL, 5=Date, 6=Impressions (mêmes posts triés différemment)
    for _, row in df.iterrows():
        if len(row) < 7:
            continue
        url_raw = row.iloc[4]
        url = str(url_raw).strip() if url_raw is not None and str(url_raw).lower() != "nan" else None
        if not url:
            continue
        aid = _extract_activity_id(url)
        if not aid:
            continue
        impressions = _safe_int(row.iloc[6])
        if aid in by_url:
            by_url[aid]["impressions"] = impressions
            # Date plus fiable depuis tableau 2 si absente
            if not by_url[aid]["date"]:
                by_url[aid]["date"] = _parse_french_date(row.iloc[5])
        else:
            by_url[aid] = {
                "url": url,
                "activity_id": aid,
                "date": _parse_french_date(row.iloc[5]),
                "interactions": None,
                "impressions": impressions,
            }

    return list(by_url.values())


def _parse_followers_sheet(xlsx_path: Path, sheet_name: str) -> tuple[list[tuple[str, int]], int | None]:
    """Renvoie (liste de (date, new_followers), total à la dernière date)."""
    import pandas as pd  # noqa: PLC0415

    df_raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine="openpyxl", header=None)
    if df_raw.empty:
        return [], None

    # Row 0 = "Nombre total d'abonnés le DD/MM/YYYY : 1005"
    total: int | None = None
    if len(df_raw) > 0:
        total_cell = df_raw.iloc[0, 1] if df_raw.shape[1] > 1 else None
        total = _safe_int(total_cell)

    # Données à partir de row 3 (row 2 = header, row 1 = vide)
    rows: list[tuple[str, int]] = []
    for idx in range(3, len(df_raw)):
        date_iso = _parse_french_date(df_raw.iloc[idx, 0])
        new_fol = _safe_int(df_raw.iloc[idx, 1] if df_raw.shape[1] > 1 else None)
        if date_iso is not None and new_fol is not None:
            rows.append((date_iso, new_fol))
    return rows, total


def _parse_demographics_sheet(xlsx_path: Path, sheet_name: str) -> list[tuple[str, str, float]]:
    """Renvoie une liste de (dimension, value, percentage). Header sur row 0."""
    import pandas as pd  # noqa: PLC0415

    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine="openpyxl", header=0)
    if df.empty:
        return []

    # Colonnes attendues : 'Principales données démographiques', 'Valeur', 'Pourcentage'
    out: list[tuple[str, str, float]] = []
    cols = list(df.columns)
    if len(cols) < 3:
        return out
    for _, row in df.iterrows():
        dim = row.iloc[0]
        val = row.iloc[1]
        pct = row.iloc[2]
        if dim is None or val is None or pct is None:
            continue
        try:
            pct_f = float(pct)
        except (ValueError, TypeError):
            continue
        if str(dim).lower() in ("nan", "none") or str(val).lower() in ("nan", "none"):
            continue
        out.append((str(dim).strip(), str(val).strip(), pct_f))
    return out


# ──────────────────────────────────────────────────────────────
# Match & insert posts
# ──────────────────────────────────────────────────────────────
def _match_or_create_post(post: dict) -> int:
    """Retourne post_id en DB. Crée un post status='external' si pas existant.

    Cascade de matching (du plus sûr au plus heuristique) :
    1. linkedin_activity_id exact — rempli au 1er match par date, fiable ensuite.
    2. linkedin_post_id LIKE %aid% — legacy : doublons 'external' déjà créés avec
       urn:li:activity:N comme linkedin_post_id.
    3. Date de publication — l'export XLSX expose urn:li:activity:N alors que l'API
       de publication renvoie urn:li:ugcPost/share:M (IDs DIFFÉRENTS, aucun mapping
       arithmétique). Le pipeline garantissant 1 post/jour, le match par date est
       déterministe ; on mémorise alors l'activity_id pour les imports suivants.
    """
    init_db()
    aid = post["activity_id"]
    date_iso = post.get("date") or datetime.now().strftime("%Y-%m-%d")

    matched = find_post_by_activity_id(aid)
    if matched is not None:
        return matched

    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM posts WHERE linkedin_post_id LIKE ? AND status IN ('published','external') LIMIT 1",
            (f"%{aid}%",),
        ).fetchone()
    if row:
        set_activity_id(row[0], aid)
        return row[0]

    by_date = find_published_post_by_date(date_iso) if post.get("date") else None
    if by_date is not None:
        set_activity_id(by_date, aid)
        print(f"[import] match par date {date_iso} → post #{by_date} (activity {aid})", file=sys.stderr)
        return by_date

    with _conn() as conn:
        # Pas trouvé → create as external (post manuel ou import historique)
        cur = conn.execute(
            """INSERT INTO posts (published_at, topic, slug, format, keywords,
                                  linkedin_post_id, linkedin_comment_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                date_iso + "T00:00:00",
                f"[external] {post['url'][:100]}",
                f"external-{aid}",
                "unknown",
                "[]",
                f"urn:li:activity:{aid}",
                None,
                "external",
            ),
        )
        return cur.lastrowid


def heal_external_duplicates() -> dict:
    """Répare les dégâts du matching legacy : fusionne chaque post 'external' dont la date
    correspond à un post 'published' (= doublon créé parce que l'activity_id de l'export
    ne matchait pas l'urn ugcPost/share), et purge les lignes analytics non-monotones
    héritées des imports fenêtrés. Idempotent."""
    init_db()
    merged = 0
    with _conn() as conn:
        externals = conn.execute(
            "SELECT id, date(published_at) FROM posts WHERE status = 'external'"
        ).fetchall()
    for ext_id, ext_date in externals:
        target = find_published_post_by_date(ext_date)
        if target is not None:
            merge_post_into(ext_id, target)
            merged += 1
            print(f"[heal] external #{ext_id} ({ext_date}) fusionné dans post #{target}", file=sys.stderr)
    purged = purge_non_monotonic_analytics()
    if purged:
        print(f"[heal] {purged} lignes analytics fenêtrées (non-monotones) purgées", file=sys.stderr)
    return {"externals_merged": merged, "rows_purged": purged}


# ──────────────────────────────────────────────────────────────
# Main : import_xlsx
# ──────────────────────────────────────────────────────────────
def import_xlsx(path: Path) -> dict:  # noqa: PLR0912, PLR0915
    """Import un XLSX LinkedIn FR. Returns summary dict."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("pandas + openpyxl requis (cf. requirements.txt)") from e

    import pandas as pd  # noqa: PLC0415

    xls = pd.ExcelFile(path, engine="openpyxl")
    print(f"[import] feuilles détectées : {xls.sheet_names}", file=sys.stderr)

    # Auto-réparation des doublons external→published créés par le matching legacy
    # (et purge des valeurs fenêtrées). No-op si rien à réparer.
    heal_external_duplicates()

    summary = {
        "posts_matched": 0,
        "posts_external_created": 0,
        "metrics_written": 0,
        "metrics_skipped_windowed": 0,
        "follower_days_imported": 0,
        "demo_rows_imported": 0,
        "warnings": [],
    }

    # ── 1. MEILLEURS POSTS ──
    posts_sheet = _find_sheet(xls, SHEET_POSTS)
    if posts_sheet:
        try:
            posts = _parse_top_posts_sheet(path, posts_sheet)
            print(f"[import] {posts_sheet}: {len(posts)} posts détectés", file=sys.stderr)
            for p in posts:
                # Match ou create
                pre_existing = False
                with _conn() as conn:
                    row = conn.execute(
                        "SELECT id, status FROM posts WHERE linkedin_post_id LIKE ? LIMIT 1",
                        (f"%{p['activity_id']}%",),
                    ).fetchone()
                    pre_existing = row is not None
                post_id = _match_or_create_post(p)
                if pre_existing:
                    summary["posts_matched"] += 1
                else:
                    summary["posts_external_created"] += 1
                # Métriques (rejet monotone : un export fenêtré ne doit pas écraser le cumul)
                if p["impressions"] is not None:
                    if upsert_analytics(post_id, "IMPRESSION", p["impressions"]):
                        summary["metrics_written"] += 1
                    else:
                        summary["metrics_skipped_windowed"] += 1
                if p["interactions"] is not None:
                    if upsert_analytics(post_id, "INTERACTION", p["interactions"]):
                        summary["metrics_written"] += 1
                    else:
                        summary["metrics_skipped_windowed"] += 1
        except (KeyError, ValueError, AttributeError, IndexError, OSError) as e:
            # Parser pandas / cellule manquante / format LinkedIn modifié — on garde le warning
            summary["warnings"].append(f"posts sheet failed: {e}")
            print(f"[import] WARN posts: {e}", file=sys.stderr)
    else:
        summary["warnings"].append(f"feuille posts introuvable (tried: {SHEET_POSTS})")

    # ── 2. ABONNÉS ──
    followers_sheet = _find_sheet(xls, SHEET_FOLLOWERS)
    if followers_sheet:
        try:
            growth_rows, total = _parse_followers_sheet(path, followers_sheet)
            print(f"[import] {followers_sheet}: {len(growth_rows)} jours, total={total}", file=sys.stderr)
            for date_iso, new_fol in growth_rows:
                # On stocke le total uniquement sur la date la plus récente (= date d'export)
                t = total if date_iso == max(d for d, _ in growth_rows) else None
                upsert_follower_growth(date_iso, new_fol, t)
                summary["follower_days_imported"] += 1
        except (KeyError, ValueError, AttributeError, IndexError, OSError) as e:
            # Parser pandas / cellule manquante / format LinkedIn modifié — on garde le warning
            summary["warnings"].append(f"followers sheet failed: {e}")
            print(f"[import] WARN followers: {e}", file=sys.stderr)
    else:
        summary["warnings"].append(f"feuille abonnés introuvable (tried: {SHEET_FOLLOWERS})")

    # ── 3. DONNÉES DÉMOGRAPHIQUES ──
    demo_sheet = _find_sheet(xls, SHEET_DEMO)
    if demo_sheet:
        try:
            demo_rows = _parse_demographics_sheet(path, demo_sheet)
            print(f"[import] {demo_sheet}: {len(demo_rows)} lignes démo", file=sys.stderr)
            if demo_rows:
                snapshot_at = datetime.now().strftime("%Y-%m-%d")
                insert_audience_snapshot(snapshot_at, demo_rows)
                summary["demo_rows_imported"] = len(demo_rows)
        except (KeyError, ValueError, AttributeError, IndexError, OSError) as e:
            # Parser pandas / cellule manquante / format LinkedIn modifié — on garde le warning
            summary["warnings"].append(f"demographics sheet failed: {e}")
            print(f"[import] WARN demographics: {e}", file=sys.stderr)
    else:
        summary["warnings"].append(f"feuille démographiques introuvable (tried: {SHEET_DEMO})")

    return summary


# Backward compat : l'UI Streamlit appelle import_csv qui aliase vers import_xlsx pour les .xlsx
def import_csv(path: Path) -> dict:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        return import_xlsx(p)
    raise RuntimeError(
        f"Format non supporté : {p.suffix}. LinkedIn exporte en .xlsx — "
        "drag-drop le fichier tel quel, conversion automatique."
    )


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--heal":
        h = heal_external_duplicates()
        print(
            f"[heal] DONE — {h['externals_merged']} doublons fusionnés, "
            f"{h['rows_purged']} lignes fenêtrées purgées",
            file=sys.stderr,
        )
        return 0
    if len(argv) != 2:
        print("Usage: import_analytics_csv.py <export.xlsx> | --heal", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"[import] file not found: {path}", file=sys.stderr)
        return 1

    s = import_xlsx(path)
    print(
        f"[import] DONE — posts matched={s['posts_matched']}, "
        f"external created={s['posts_external_created']}, "
        f"metrics={s['metrics_written']} (+{s['metrics_skipped_windowed']} fenêtrées rejetées), "
        f"follower days={s['follower_days_imported']}, "
        f"demo rows={s['demo_rows_imported']}",
        file=sys.stderr,
    )
    if s["warnings"]:
        for w in s["warnings"]:
            print(f"[import] ⚠️  {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
