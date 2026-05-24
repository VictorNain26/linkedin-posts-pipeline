"""
Dashboard Streamlit — visualisation du pipeline LinkedIn.

3 pages :
- 📜 Historique : posts publiés (status='published'), preview PDF + texte + hook variants + analytics
- 📊 Analytics  : formula_win_rate, format mix, métriques agrégées
- 🧪 Tests      : posts générés en dry-run (status='test'), même vue que l'historique

Lancement :
    streamlit run dashboard.py
Accès :
    ssh -L 8501:localhost:8501 victormoi@victorserv
    puis http://localhost:8501 dans le navigateur local
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pypdfium2 as pdfium
import streamlit as st

from config import DB_PATH, LEARNINGS_PATH, OUTPUT_DIR

st.set_page_config(
    page_title="LinkedIn Posts — Dashboard",
    page_icon="📊",
    layout="wide",
)

# ──────────────────────────────────────────────────────────────
# Kill-switch publi auto (fichier flag dans DATA_DIR)
# ──────────────────────────────────────────────────────────────
PAUSE_FLAG = Path(OUTPUT_DIR).parent / ".publi_paused"


def is_paused() -> bool:
    return PAUSE_FLAG.exists()


def pause_reason() -> str:
    if not PAUSE_FLAG.exists():
        return ""
    try:
        return PAUSE_FLAG.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_paused(reason: str = "") -> None:
    PAUSE_FLAG.write_text(reason or "paused via dashboard", encoding="utf-8")


def set_resumed() -> None:
    PAUSE_FLAG.unlink(missing_ok=True)

# ──────────────────────────────────────────────────────────────
# DB helpers (cached pour limiter les re-reads à chaque rerun Streamlit)
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
            "SELECT formula, hook, is_winner, judge_reason FROM hook_variants WHERE post_id = ? ORDER BY is_winner DESC",
            conn,
            params=(post_id,),
        )


@st.cache_data(ttl=60)
def load_latest_analytics(post_id: int) -> pd.DataFrame:
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
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
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
    return df


# ──────────────────────────────────────────────────────────────
# Filesystem helpers (post output dir)
# ──────────────────────────────────────────────────────────────
def post_dir_for(published_at: str, slug: str) -> Path | None:
    try:
        date_tag = datetime.fromisoformat(published_at).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    candidate = Path(OUTPUT_DIR) / f"{date_tag}-{slug}"
    return candidate if candidate.exists() else None


@st.cache_data(ttl=300)
def render_pdf_pages(pdf_path_str: str, scale: float = 1.5) -> list:
    """Render chaque page PDF en PIL Image, cachées pour éviter re-rendering à chaque rerun."""
    pdf = pdfium.PdfDocument(pdf_path_str)
    images = []
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=scale)
        images.append(bitmap.to_pil())
    return images


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


# ──────────────────────────────────────────────────────────────
# Rendu d'un post (utilisé par les pages Historique et Tests)
# ──────────────────────────────────────────────────────────────
def render_post(row: pd.Series) -> None:
    post_id = int(row["id"])
    pdir = post_dir_for(row["published_at"], row["slug"])

    col_meta, col_metrics = st.columns([2, 1])
    with col_meta:
        st.markdown(f"**Topic** : {row['topic']}")
        st.markdown(
            f"**Date** : {row['published_at']}  ·  **Format** : `{row['format']}`  ·  "
            f"**LinkedIn URN** : `{row['linkedin_post_id'] or '—'}`"
        )

    with col_metrics:
        analytics = load_latest_analytics(post_id)
        if analytics.empty:
            st.caption("Pas de métriques importées (cf. `import_analytics_csv.py`)")
        else:
            metric_cols = st.columns(min(len(analytics), 4))
            for i, (_, m) in enumerate(analytics.iterrows()):
                with metric_cols[i % len(metric_cols)]:
                    st.metric(m["metric"], int(m["count"]))

    tab_pdf, tab_text, tab_hooks = st.tabs(["📄 Carousel PDF", "📝 Post + Commentaire", "🎣 Hook variants"])

    with tab_pdf:
        if not pdir:
            st.warning("Dossier output introuvable (peut-être nettoyé par cleanup.sh après 7j).")
        else:
            pdf_path = pdir / "carousel.pdf"
            if pdf_path.exists():
                pages = render_pdf_pages(str(pdf_path))
                cols = st.columns(min(len(pages), 3))
                for i, img in enumerate(pages):
                    with cols[i % len(cols)]:
                        st.image(img, caption=f"Slide {i+1}", use_container_width=True)
                with pdf_path.open("rb") as f:
                    st.download_button(
                        "⬇️ Télécharger le PDF",
                        f,
                        file_name=pdf_path.name,
                        mime="application/pdf",
                        key=f"dl_pdf_{post_id}",
                    )
            else:
                st.info(f"Pas de PDF pour ce post (format = `{row['format']}`).")

    with tab_text:
        if not pdir:
            st.warning("Dossier output introuvable.")
        else:
            st.subheader("Texte du post")
            st.code(read_text_file(pdir / "post.txt") or "(vide)", language="markdown")
            st.subheader("1er commentaire")
            st.code(read_text_file(pdir / "first_comment.txt") or "(vide)", language="markdown")

    with tab_hooks:
        variants = load_hook_variants(post_id)
        if variants.empty:
            st.info("Pas de hook variants enregistrés.")
        else:
            for _, v in variants.iterrows():
                badge = "🏆 winner" if v["is_winner"] else "  candidat"
                st.markdown(f"**{badge}** · `{v['formula']}` · {len(v['hook'])} chars")
                st.markdown(f"> {v['hook']}")
                if v["is_winner"] and v["judge_reason"]:
                    st.caption(f"💭 Raison du judge : {v['judge_reason']}")
                st.markdown("---")


# ──────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────
def page_historique():
    st.title("📜 Historique des posts publiés")
    posts = load_posts(status="published")
    if posts.empty:
        st.info("Aucun post publié pour le moment. Lance ton premier `./pipeline.sh` !")
        return

    st.caption(f"{len(posts)} posts publiés.")
    for _, row in posts.iterrows():
        title = f"{row['published_at'][:16]}  ·  [{row['format']}]  ·  {row['topic'][:80]}"
        with st.expander(title, expanded=False):
            render_post(row)


def page_tests():
    st.title("🧪 Posts de test (dry-run)")
    st.caption(
        "Posts générés via `./pipeline.sh --dry-run`, jamais publiés sur LinkedIn. "
        "Utile pour valider la qualité du contenu généré."
    )
    posts = load_posts(status="test")
    if posts.empty:
        st.info("Aucun test pour le moment. Lance `./pipeline.sh --dry-run` pour en générer un.")
        return

    st.caption(f"{len(posts)} tests en historique.")
    for _, row in posts.iterrows():
        title = f"{row['published_at'][:16]}  ·  [{row['format']}]  ·  {row['topic'][:80]}"
        with st.expander(title, expanded=False):
            render_post(row)


def _render_ai_analysis_section():
    """Section 'Analyse IA' au-dessus des data brutes. Affiche le résumé Sonnet
    + biases actifs + 5 recos, avec bouton de régénération."""
    st.header("🧠 Analyse — Marketing Lead B2B")

    if not LEARNINGS_PATH.exists():
        st.info(
            "Pas encore d'analyse IA générée. "
            "Min requis : 3 posts publiés (status='published' ou 'external') sur 28j. "
            "Clique sur **Regénérer** ci-dessous une fois que tu as importé un XLSX."
        )
        c1, _ = st.columns([1, 3])
        with c1:
            if st.button("🔄 Générer maintenant (~$0.10)", key="gen_learnings_empty"):
                with st.spinner("Claude Sonnet analyse..."):
                    from weekly_report import generate_learnings
                    try:
                        new_data = generate_learnings(days=28)
                        if new_data:
                            st.success("✅ Analyse générée")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.warning("Pas assez de data (< 3 posts publiés).")
                    except Exception as e:
                        st.error(f"❌ {e}")
        st.markdown("---")
        return

    try:
        data = json.loads(LEARNINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        st.error(f"learnings.json malformé : {e}")
        st.markdown("---")
        return

    # Header : metadata + bouton regen
    meta_cols = st.columns([2, 2, 2, 2])
    meta_cols[0].metric("Généré le", data.get("generated_at", "—")[:10])
    meta_cols[1].metric("Basé sur", f"{data.get('based_on_posts', 0)} posts")
    meta_cols[2].metric("Période", f"{data.get('based_on_period_days', 28)}j glissants")
    with meta_cols[3]:
        st.write("")  # spacing
        if st.button("🔄 Regénérer (~$0.10)", key="regen_learnings_section", use_container_width=True):
            with st.spinner("Claude Sonnet analyse..."):
                from weekly_report import generate_learnings
                try:
                    new_data = generate_learnings(days=28)
                    if new_data:
                        st.success("✅ Regénéré")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Pas assez de data (< 3 posts).")
                except Exception as e:
                    st.error(f"❌ {e}")

    # Résumé
    st.info(data.get("summary", "—"))

    # Biases auto + Recos manuelles côte à côte
    col_biases, col_recos = st.columns([1, 1])

    with col_biases:
        biases = data.get("biases", [])
        st.markdown(f"#### ⚙️ Biases injectés dans le pipeline ({len(biases)}/5)")
        if not biases:
            st.caption(
                "Aucun bias détecté avec assez de confiance. Le pipeline tourne sur les defaults. "
                "Plus tu publieras de posts avec analytics, plus l'IA pourra détecter des patterns."
            )
        else:
            for b in biases:
                with st.container(border=True):
                    st.markdown(f"`{b.get('type', '?')}` · **{b.get('key', '?')}**")
                    st.markdown(b.get("instruction", "—"))
                    st.caption(f"_{b.get('evidence', '')}_")

    with col_recos:
        recs = data.get("recommendations", [])
        st.markdown(f"#### 💡 Recommandations actionnables ({len(recs)}/5)")
        if not recs:
            st.caption("Aucune reco cette semaine.")
        else:
            for i, r in enumerate(recs, 1):
                st.markdown(f"**{i}.** {r}")

    with st.expander("🔍 JSON brut (debug / édition manuelle)"):
        st.json(data)
        st.caption(f"Path : `{LEARNINGS_PATH}` — éditable à la main pour override.")

    st.markdown("---")


def page_analytics():
    st.title("📊 Analytics")
    st.caption(
        "Analyse IA + métriques live + démographie audience. "
        "Workflow : exporte ton XLSX LinkedIn (lien dans la section 📤 en bas), drag-drop le, "
        "regénère l'analyse. Le pipeline applique ensuite les biases auto à chaque post."
    )

    # ──────────────────────────────────────────────────────────
    # 1. ANALYSE IA en premier (executive summary)
    # ──────────────────────────────────────────────────────────
    _render_ai_analysis_section()

    # ──────────────────────────────────────────────────────────
    # 2. Fenêtre temporelle
    # ──────────────────────────────────────────────────────────
    days = st.slider("Fenêtre d'analyse (jours)", min_value=7, max_value=365, value=90, step=7)

    # ──────────────────────────────────────────────────────────
    # 3. KPIs globaux engagement + growth (côte à côte)
    # ──────────────────────────────────────────────────────────
    posts_metrics = load_post_metrics_summary(days=days)
    with sqlite3.connect(DB_PATH) as conn:
        growth = pd.read_sql_query(
            """SELECT date, new_followers, total_followers
               FROM follower_growth WHERE date > date('now', ? || ' days')
               ORDER BY date""",
            conn, params=(f"-{days}",),
        )

    st.header(f"📈 Performance — {days} derniers jours")

    if posts_metrics.empty and growth.empty:
        st.info(
            "Aucune data sur cette fenêtre. "
            "Importe un XLSX LinkedIn (section 📤 en bas) pour peupler le dashboard."
        )
    else:
        # 4 KPIs principaux engagement
        if not posts_metrics.empty:
            kpi_cols = st.columns(5)
            kpi_cols[0].metric("Posts", len(posts_metrics))
            kpi_cols[1].metric("Impressions", int(posts_metrics["impressions"].fillna(0).sum()))
            kpi_cols[2].metric("Reactions", int(posts_metrics["reactions"].fillna(0).sum()))
            kpi_cols[3].metric("Comments", int(posts_metrics["comments"].fillna(0).sum()))
            kpi_cols[4].metric("Saves (360Brew #1)", int(posts_metrics["saves"].fillna(0).sum()))

        # 3 KPIs croissance abonnés (si data dispo)
        if not growth.empty:
            growth["date"] = pd.to_datetime(growth["date"])
            last_total = growth["total_followers"].dropna().iloc[-1] if growth["total_followers"].notna().any() else None
            k1, k2, k3 = st.columns(3)
            k1.metric("Total abonnés", f"{int(last_total)}" if last_total else "—")
            k2.metric(f"Nouveaux sur {days}j", int(growth["new_followers"].sum()))
            k3.metric("Moyenne /jour", f"{growth['new_followers'].mean():.1f}")
            st.bar_chart(growth.set_index("date")["new_followers"], height=200)

    # ──────────────────────────────────────────────────────────
    # 4. Démographie audience
    # ──────────────────────────────────────────────────────────
    st.header("🌍 Audience")
    with sqlite3.connect(DB_PATH) as conn:
        last_ts = conn.execute("SELECT MAX(snapshot_at) FROM audience_snapshot").fetchone()[0]
    if not last_ts:
        st.caption("Pas de data démographique — importe un XLSX pour activer.")
    else:
        st.caption(f"Snapshot du {last_ts[:16]}")
        with sqlite3.connect(DB_PATH) as conn:
            demo = pd.read_sql_query(
                """SELECT dimension, value, percentage FROM audience_snapshot
                   WHERE snapshot_at = ? ORDER BY dimension, percentage DESC""",
                conn, params=(last_ts,),
            )
        dimensions = demo["dimension"].unique().tolist()
        cols = st.columns(min(len(dimensions), 3) or 1)
        for i, dim in enumerate(dimensions):
            sub = demo[demo["dimension"] == dim].head(5)
            with cols[i % len(cols)]:
                st.markdown(f"**{dim}**")
                for _, row in sub.iterrows():
                    pct = row["percentage"] * 100
                    st.markdown(f"- {row['value']} — `{pct:.1f}%`")

    # ──────────────────────────────────────────────────────────
    # 5. Détail par post
    # ──────────────────────────────────────────────────────────
    if not posts_metrics.empty:
        st.header("📋 Posts détaillés")
        st.dataframe(
            posts_metrics[["published_at", "topic", "format", "impressions", "reactions", "comments", "reshares", "saves"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "published_at": st.column_config.DatetimeColumn("Date", format="YYYY-MM-DD HH:mm"),
                "topic": st.column_config.TextColumn("Topic", width="large"),
                "format": "Format",
                "impressions": st.column_config.NumberColumn("Impr.", format="%d"),
                "reactions": st.column_config.NumberColumn("Reactions", format="%d"),
                "comments": st.column_config.NumberColumn("Comments", format="%d"),
                "reshares": st.column_config.NumberColumn("Reshares", format="%d"),
                "saves": st.column_config.NumberColumn("Saves", format="%d"),
            },
        )

    # ──────────────────────────────────────────────────────────
    # 6. Performance par formule de hook + format
    # ──────────────────────────────────────────────────────────
    st.header("🎯 Patterns gagnants")
    col_fa, col_fb = st.columns(2)

    with col_fa:
        st.subheader("Formule de hook")
        formula = load_formula_stats(days=days)
        if formula.empty:
            st.caption("Pas de data — il faut posts publiés + analytics importés.")
        else:
            tab_pick, tab_impr = st.tabs(["# de fois gagnante", "Impressions moy."])
            with tab_pick:
                st.bar_chart(formula.set_index("formula")["picked_count"])
            with tab_impr:
                st.bar_chart(formula.set_index("formula")["avg_impressions"])

    with col_fb:
        st.subheader("Distribution formats")
        fmt = load_format_distribution(days=days)
        if fmt.empty:
            st.caption("Pas de data.")
        else:
            st.bar_chart(fmt.set_index("format")["n"])

    # ──────────────────────────────────────────────────────────
    # 7. Import XLSX (workflow hebdo)
    # ──────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📤 Importer un export LinkedIn Analytics (XLSX hebdo)", expanded=False):
        st.markdown(
            "**Workflow** : récupère ton XLSX depuis [LinkedIn Analytics → Posts → Export]"
            "(https://www.linkedin.com/analytics/creator), drag-drop ci-dessous, "
            "puis clique **Regénérer l'analyse IA** en haut. Tout est persisté en DB."
        )
        uploaded = st.file_uploader(
            "Drag & drop XLSX/CSV",
            type=["xlsx", "xls", "csv"],
            key="analytics_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            upload_dir = Path(OUTPUT_DIR).parent / "uploads"
            upload_dir.mkdir(exist_ok=True)
            saved_path = upload_dir / uploaded.name
            saved_path.write_bytes(uploaded.getbuffer())

            try:
                from import_analytics_csv import import_csv as run_import
                with st.spinner("Import en cours..."):
                    summary = run_import(saved_path)
                cols = st.columns(4)
                cols[0].metric("Posts matchés", summary.get("posts_matched", 0))
                cols[1].metric("Posts externes créés", summary.get("posts_external_created", 0))
                cols[2].metric("Métriques inscrites", summary.get("metrics_written", 0))
                cols[3].metric("Jours followers", summary.get("follower_days_imported", 0))
                if summary.get("warnings"):
                    for w in summary["warnings"]:
                        st.warning(w)
                if st.button("🔄 Rafraîchir + suggérer regen IA", key="refresh_after_import"):
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Erreur d'import : {e}")


# ──────────────────────────────────────────────────────────────
# Sidebar nav
# ──────────────────────────────────────────────────────────────
def page_learnings():
    st.title("🧠 Learnings — bias auto injectés dans le pipeline")
    st.caption(
        "Généré chaque lundi 7h par `weekly_report.sh` via Claude Sonnet. "
        "Le fichier `state/learnings.json` est lu par le pipeline au moment de générer un post, "
        "et injecté comme 4e block system (cache_control: ephemeral). "
        "**TTL 14j** : au-delà, learnings ignorés tant que pas regénérés."
    )

    if not LEARNINGS_PATH.exists():
        st.info(
            "Pas de `learnings.json` pour l'instant. "
            "Lance `python3 weekly_report.py` (ou attends lundi 7h) pour générer la 1re analyse. "
            "Min requis : 3 posts publiés sur 28j."
        )
        return

    try:
        data = json.loads(LEARNINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        st.error(f"learnings.json malformé : {e}")
        return

    # Métadonnées
    col1, col2, col3 = st.columns(3)
    col1.metric("Généré le", data.get("generated_at", "—")[:10])
    col2.metric("Basé sur", f"{data.get('based_on_posts', 0)} posts")
    col3.metric("Période", f"{data.get('based_on_period_days', 28)}j glissants")

    st.subheader("Résumé")
    st.info(data.get("summary", "—"))

    # Biases
    biases = data.get("biases", [])
    st.subheader(f"Biases appliqués au pipeline ({len(biases)}/5)")
    if not biases:
        st.caption("Aucun bias — pipeline tourne sur les defaults.")
    else:
        for b in biases:
            with st.container(border=True):
                cols = st.columns([1, 1, 3, 2])
                cols[0].markdown(f"`{b.get('type', '?')}`")
                cols[1].markdown(f"**{b.get('key', '?')}**")
                cols[2].markdown(b.get("instruction", "—"))
                cols[3].caption(f"_{b.get('evidence', '')}_")

    # Recommandations
    recs = data.get("recommendations", [])
    st.subheader(f"Recommandations actionnables ({len(recs)}/5)")
    if not recs:
        st.caption("Aucune reco cette semaine.")
    else:
        for i, r in enumerate(recs, 1):
            st.markdown(f"**{i}.** {r}")

    st.markdown("---")
    st.subheader("⚙️ Actions")
    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("🔄 Regénérer maintenant (~$0.10)", key="regen_learnings"):
            with st.spinner("Analyse Claude Sonnet en cours..."):
                from weekly_report import generate_learnings
                try:
                    new_data = generate_learnings(days=28)
                    if new_data:
                        st.success("✅ learnings.json regénéré")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Pas assez de data (< 3 posts publiés) — learnings non écrit")
                except Exception as e:
                    st.error(f"❌ {e}")
    with col_b:
        st.code(f"Path : {LEARNINGS_PATH}", language="text")
        st.caption("Éditable manuellement à la main si tu veux override un bias.")

    # JSON brut (collapsé)
    with st.expander("🔍 JSON brut (pour debug ou édition manuelle)"):
        st.json(data)


PAGES = {
    "📊 Analytics + IA": page_analytics,
    "📜 Historique posts publiés": page_historique,
    "🧪 Tests (dry-run)": page_tests,
}

st.sidebar.title("LinkedIn Pipeline")
st.sidebar.caption("Dashboard — Victor Lenain")
choice = st.sidebar.radio("Pages", list(PAGES.keys()), label_visibility="collapsed")

st.sidebar.markdown("---")

# Toggle publi auto (kill-switch)
st.sidebar.subheader("⚙️ Publi auto")
paused_now = is_paused()
status_emoji = "⏸️ EN PAUSE" if paused_now else "▶️ ACTIVE"
status_color = "red" if paused_now else "green"
st.sidebar.markdown(f"État : :{status_color}[**{status_emoji}**]")

if paused_now:
    reason = pause_reason()
    if reason:
        st.sidebar.caption(f"Motif : _{reason}_")
    if st.sidebar.button("▶️ Réactiver la publi auto", type="primary"):
        set_resumed()
        st.rerun()
else:
    reason_input = st.sidebar.text_input(
        "Motif (optionnel)", placeholder="ex: vacances, refonte contenu...", key="pause_reason"
    )
    if st.sidebar.button("⏸️ Mettre en pause", type="secondary"):
        set_paused(reason_input)
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(f"DB : `{DB_PATH}`")
st.sidebar.caption(f"Output : `{OUTPUT_DIR}`")

if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()
    st.rerun()

# Banner d'alerte en haut de chaque page si la publi est en pause
if is_paused():
    reason = pause_reason()
    suffix = f" — _{reason}_" if reason else ""
    st.error(f"⏸️ **PUBLI AUTO EN PAUSE** : aucun post ne sera publié par le cron mar/mer/jeu 10h30.{suffix}")

PAGES[choice]()
