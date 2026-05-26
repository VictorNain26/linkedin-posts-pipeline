"""
Rapport hebdomadaire LinkedIn — synthèse semaine N-1, envoi par email.

Inputs : posts + analytics + hook_variants depuis SQLite.
Output : markdown sauvé sur disque + envoyé via Gmail SMTP.

Auth Gmail SMTP : utilise un mot de passe d'application Google
(env var GMAIL_APP_PASSWORD) — pas le mot de passe principal.
Doc : https://support.google.com/accounts/answer/185833
"""

import json
import os
import smtplib
import sqlite3
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

from anthropic_client import call_tool
from config import (
    DATA_DIR,
    DB_PATH,
    GMAIL_SMTP_PORT,
    GMAIL_SMTP_SERVER,
    LEARNINGS_PATH,
    SONNET_MODEL,
    WEEKLY_REPORT_RECIPIENT,
    system_voice,
)
from history import (
    follower_growth_summary,
    formula_win_rate,
    latest_audience_snapshot,
    posts_in_week,
)

# ──────────────────────────────────────────────────────────────
# Tool schema pour l'analyse hebdo Claude — borne MAX 5 biases
# ──────────────────────────────────────────────────────────────
LEARNINGS_TOOL = {
    "name": "submit_weekly_learnings",
    "description": (
        "Submit weekly marketing analysis as structured learnings + recommendations. "
        "Replaces previous learnings.json entirely — no continuity forced."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 500,
                "description": "2-3 phrases résumant la perf de la semaine et la trend",
            },
            "based_on_posts": {"type": "integer", "minimum": 0},
            "based_on_period_days": {"type": "integer", "minimum": 1, "maximum": 90},
            "biases": {
                "type": "array",
                "minItems": 0,
                "maxItems": 5,
                "description": "Biases à injecter dans le pipeline. MAX 5. Si plus de 5 candidats, garde les + impactants.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Identifier court ex: formula_prospect_question_top",
                        },
                        "type": {
                            "type": "string",
                            "enum": [
                                "formula_weight",
                                "topic_push",
                                "topic_avoid",
                                "format_target",
                                "audience_focus",
                            ],
                        },
                        "key": {"type": "string", "description": "Sujet/formule/format concerné"},
                        "instruction": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Instruction concrète pour les agents (ex: 'privilégier prospect_question +50%')",
                        },
                        "evidence": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Chiffre/observation qui justifie ce bias",
                        },
                    },
                    "required": ["id", "type", "key", "instruction", "evidence"],
                },
            },
            "recommendations": {
                "type": "array",
                "minItems": 0,
                "maxItems": 5,
                "description": "Recos data-driven actionnables pour la semaine suivante. MAX 5.",
                "items": {"type": "string", "maxLength": 300},
            },
        },
        "required": ["summary", "based_on_posts", "based_on_period_days", "biases", "recommendations"],
    },
}


def _collect_data_for_analysis(days: int = 28) -> dict:
    """Pull les data nécessaires à l'analyse Sonnet, sérialisable JSON."""
    with sqlite3.connect(DB_PATH) as conn:
        # Posts published + external (avec métriques) sur la fenêtre
        posts_rows = conn.execute(
            """SELECT p.id, p.published_at, p.format, p.status, p.topic,
                      MAX(CASE WHEN pa.metric='IMPRESSION' THEN pa.count END) as impr,
                      MAX(CASE WHEN pa.metric='INTERACTION' THEN pa.count END) as inter
               FROM posts p
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE p.published_at > datetime('now', ? || ' days')
                 AND p.status IN ('published', 'external')
               GROUP BY p.id
               ORDER BY p.published_at DESC""",
            (f"-{days}",),
        ).fetchall()

        # Hook variants gagnants
        hooks_rows = conn.execute(
            """SELECT hv.formula, COUNT(*) as picked,
                      COALESCE(AVG(CASE WHEN pa.metric='IMPRESSION' THEN pa.count END), 0) as avg_impr
               FROM hook_variants hv
               JOIN posts p ON p.id = hv.post_id
               LEFT JOIN post_analytics pa ON pa.post_id = p.id
               WHERE hv.is_winner = 1
                 AND p.published_at > datetime('now', ? || ' days')
                 AND p.status = 'published'
               GROUP BY hv.formula""",
            (f"-{days}",),
        ).fetchall()

    posts = [
        {
            "id": r[0], "date": r[1][:10], "format": r[2], "status": r[3],
            "topic": (r[4] or "")[:120],
            "impressions": r[5], "interactions": r[6],
        }
        for r in posts_rows
    ]

    growth = follower_growth_summary(days=days)
    demo = latest_audience_snapshot()
    # Top 3 par dimension
    demo_top = {dim: vals[:3] for dim, vals in demo.items()}

    hook_stats = [
        {"formula": r[0], "picked": r[1], "avg_impressions": int(r[2])}
        for r in hooks_rows
    ]

    return {
        "period_days": days,
        "posts_count": len(posts),
        "posts": posts,
        "follower_growth": growth,
        "audience_top_demo": demo_top,
        "hook_formulas_perf": hook_stats,
    }


def generate_learnings(days: int = 28) -> dict:
    """Appelle Claude Sonnet pour générer learnings + recommandations, persiste en JSON.

    Renvoie le dict learnings. Si data insuffisante (< 3 posts), retourne dict vide
    et n'écrit pas learnings.json (évite biases sur petit échantillon).
    """
    data = _collect_data_for_analysis(days=days)
    if data["posts_count"] < 3:
        print(
            f"[learnings] Skipping IA analysis — only {data['posts_count']} posts sur {days}j "
            "(seuil min = 3, évite biases sur petit échantillon).",
            file=sys.stderr,
        )
        return {}

    data_json = json.dumps(data, ensure_ascii=False, indent=2)
    print(f"[learnings] data context : {len(data_json)} chars", file=sys.stderr)

    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(),
        user_text=(
            "<role>\n"
            "Tu es Marketing Lead B2B pour le compte LinkedIn de Victor. "
            "Tu analyses les data hebdo pour produire des learnings ACTIONNABLES "
            "que le pipeline injectera AUTOMATIQUEMENT dans les prochains posts.\n"
            "</role>\n\n"
            "<task>\n"
            "1. Identifie 0-5 BIASES à appliquer (formules à privilégier, sujets à push/éviter, "
            "format ratio à ajuster, segment audience à viser).\n"
            "2. Fournis 0-5 RECOMMANDATIONS data-driven pour la semaine suivante "
            "(actions humaines à envisager, complément des biases auto).\n"
            "3. Résume la perf en 2-3 phrases.\n"
            "</task>\n\n"
            "<critical>\n"
            "Ton output REMPLACE intégralement le learnings.json précédent. "
            "Si un bias n'est plus supporté par les data récentes, NE LE RÉINCLUS PAS. "
            "Confidence threshold : pas de bias appuyé sur < 3 observations.\n\n"
            "Évite les évidences ('publier plus pour avoir plus d'impressions'). "
            "Cherche les PATTERNS non triviaux. Sois honnête : si rien d'actionnable ne ressort, "
            "renvoie biases=[] et recommendations=[].\n"
            "</critical>\n\n"
            "<data_input>\n"
            + data_json
            + "\n</data_input>"
        ),
        tool=LEARNINGS_TOOL,
        max_tokens=2000,
    )

    # Enrich avec metadata
    learnings = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **out,
    }

    LEARNINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEARNINGS_PATH.write_text(json.dumps(learnings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[learnings] ✅ saved to {LEARNINGS_PATH}", file=sys.stderr)
    return learnings


def _render_learnings_section(learnings: dict) -> str:
    """Rend la section markdown 'Analyse Claude' pour le rapport hebdo."""
    if not learnings:
        return "\n## 🧠 Analyse IA — Marketing Lead B2B\n\n_Pas assez de data publiée pour générer l'analyse (seuil min = 3 posts publiés)._\n"

    lines = ["\n## 🧠 Analyse IA — Marketing Lead B2B\n"]
    lines.append(f"_Basée sur {learnings.get('based_on_posts', 0)} posts sur {learnings.get('based_on_period_days', 28)} jours_\n")
    lines.append(f"**Résumé** : {learnings.get('summary', '—')}\n")

    biases = learnings.get("biases", [])
    if biases:
        lines.append("### Biases appliqués automatiquement au pipeline (max 5)")
        lines.append("| Type | Cible | Instruction | Evidence |")
        lines.append("|---|---|---|---|")
        for b in biases:
            lines.append(
                f"| `{b['type']}` | {b['key']} | {b['instruction']} | _{b['evidence']}_ |"
            )
        lines.append("")
    else:
        lines.append("_Aucun bias automatique appliqué cette semaine._\n")

    recs = learnings.get("recommendations", [])
    if recs:
        lines.append("### Recommandations actionnables (à décider par toi)")
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. {r}")
        lines.append("")

    lines.append(f"_Learnings actifs dans `{LEARNINGS_PATH}` — éditables manuellement, regénérés chaque lundi 7h._\n")
    return "\n".join(lines)

load_dotenv()

REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def _week_iso(target_date: date) -> tuple[int, int]:
    iso = target_date.isocalendar()
    return iso.year, iso.week


def _format_metric(metrics: dict[str, int], name: str) -> str:
    return str(metrics.get(name, 0))


def _render_markdown(year: int, week: int, posts: list[dict], winners: dict) -> str:
    if not posts:
        return (
            f"# Rapport LinkedIn — semaine {week:02d}/{year}\n\n"
            "Aucun post publié cette semaine.\n"
            "Causes possibles : RSS sec, healthcheck KO, ou token expiré.\n"
        )

    lines: list[str] = [f"# Rapport LinkedIn — semaine {week:02d}/{year}\n"]

    # ── Totals ────────────────────────────────────────────
    total_impressions = sum(p["analytics"].get("IMPRESSION", 0) for p in posts)
    total_reach = sum(p["analytics"].get("MEMBERS_REACHED", 0) for p in posts)
    total_reactions = sum(p["analytics"].get("REACTION", 0) for p in posts)
    total_comments = sum(p["analytics"].get("COMMENT", 0) for p in posts)
    total_saves = sum(p["analytics"].get("POST_SAVE", 0) for p in posts)
    total_clicks = sum(p["analytics"].get("LINK_CLICKS", 0) for p in posts)
    total_profile_views = sum(p["analytics"].get("PROFILE_VIEW_FROM_CONTENT", 0) for p in posts)
    total_followers_gained = sum(p["analytics"].get("FOLLOWER_GAINED_FROM_CONTENT", 0) for p in posts)

    lines.append("## Vue d'ensemble\n")
    lines.append(f"- Posts publiés : **{len(posts)}**")
    lines.append(f"- Impressions totales : **{total_impressions:,}**".replace(",", " "))
    lines.append(f"- Reach unique : **{total_reach:,}**".replace(",", " "))
    lines.append(f"- Réactions : **{total_reactions}**")
    lines.append(f"- Commentaires : **{total_comments}**")
    lines.append(f"- Saves (signal algo fort en 2026) : **{total_saves}**")
    lines.append(f"- Link clicks : **{total_clicks}**")
    lines.append(f"- Vues profil depuis posts : **{total_profile_views}**")
    lines.append(f"- Followers gagnés depuis posts : **{total_followers_gained}**")
    lines.append("")

    # ── Posts détaillés ──────────────────────────────────
    lines.append("## Détail des posts\n")
    for p in posts:
        a = p["analytics"]
        lines.append(f"### {p['mode']} / {p['format']} — {p['published_at'][:10]}")
        lines.append(f"- **Topic** : {p['topic']}")
        lines.append(f"- LinkedIn URN : `{p['linkedin_post_id']}`")
        lines.append(
            f"- Métriques : {_format_metric(a, 'IMPRESSION')} imp · "
            f"{_format_metric(a, 'MEMBERS_REACHED')} reach · "
            f"{_format_metric(a, 'REACTION')} réactions · "
            f"{_format_metric(a, 'COMMENT')} comments · "
            f"{_format_metric(a, 'POST_SAVE')} saves · "
            f"{_format_metric(a, 'LINK_CLICKS')} clicks"
        )
        lines.append("")

    # ── Apprentissages (formules de hook) ──────────────
    if winners:
        lines.append("## Apprentissages — quelle formule de hook gagne ?\n")
        lines.append("| Formule | Picked (90j) | Impressions moy. |")
        lines.append("|---|---|---|")
        for formula, stats in sorted(winners.items(), key=lambda x: -x[1]["picked"]):
            lines.append(f"| {formula} | {stats['picked']} | {stats['avg_impressions']:,}".replace(",", " ") + " |")
        lines.append("")

    # ── Best post ─────────────────────────────────────────
    best = max(posts, key=lambda p: p["analytics"].get("IMPRESSION", 0))
    lines.append("## Best post de la semaine\n")
    lines.append(f"**{best['topic']}** ({best['mode']} / {best['format']})")
    lines.append(f"- {best['analytics'].get('IMPRESSION', 0)} impressions")
    lines.append(f"- {best['analytics'].get('POST_SAVE', 0)} saves")
    lines.append(f"- {best['analytics'].get('COMMENT', 0)} commentaires")
    lines.append("")

    lines.append("---")
    lines.append(f"_Rapport généré le {datetime.now().isoformat(timespec='minutes')}_")
    return "\n".join(lines)


def _send_email(subject: str, body: str, recipient: str) -> bool:
    """Envoi via Gmail SMTP avec app password.
    Renvoie True si OK, False si pas de credentials (no-op silencieux côté envoi
    mais le rapport est toujours sauvegardé sur disque)."""
    sender = os.environ.get("GMAIL_SENDER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not sender or not password:
        print(
            "[weekly-report] GMAIL_SENDER or GMAIL_APP_PASSWORD missing — skipping email "
            "(report saved on disk only)",
            file=sys.stderr,
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(GMAIL_SMTP_SERVER, GMAIL_SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    print(f"[weekly-report] email sent to {recipient}", file=sys.stderr)
    return True


def generate_for_last_week() -> Path:
    """Génère le rapport de la semaine ISO précédente (lundi-dimanche).

    Inclut l'analyse Claude Sonnet (learnings + recos) qui écrit aussi
    state/learnings.json — utilisé par le pipeline pour bias les prochains posts.
    """
    today = date.today()
    last_week_anchor = today - timedelta(days=7)
    year, week = _week_iso(last_week_anchor)

    posts = posts_in_week(year, week)
    winners = formula_win_rate(days=90)
    markdown = _render_markdown(year, week, posts, winners)

    # ── Analyse Claude Sonnet → learnings.json + section markdown ──
    try:
        learnings = generate_learnings(days=28)
    except (RuntimeError, KeyError, ValueError, OSError) as e:
        # Anthropic API échec / schema mismatch / write learnings.json échec → on continue avec rapport vide
        print(f"[weekly-report] WARN: learnings generation failed: {e}", file=sys.stderr)
        learnings = {}
    markdown += _render_learnings_section(learnings)

    report_path = REPORTS_DIR / f"linkedin-week-{year}-{week:02d}.md"
    report_path.write_text(markdown, encoding="utf-8")
    print(f"[weekly-report] saved to {report_path}", file=sys.stderr)

    subject = f"[LinkedIn] Rapport semaine {week:02d}/{year} — {len(posts)} posts"
    _send_email(subject, markdown, WEEKLY_REPORT_RECIPIENT)
    return report_path


if __name__ == "__main__":
    path = generate_for_last_week()
    print(path)
