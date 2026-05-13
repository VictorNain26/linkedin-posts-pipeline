"""
Rapport hebdomadaire LinkedIn — synthèse semaine N-1, envoi par email.

Inputs : posts + analytics + hook_variants depuis SQLite.
Output : markdown sauvé sur disque + envoyé via Gmail SMTP.

Auth Gmail SMTP : utilise un mot de passe d'application Google
(env var GMAIL_APP_PASSWORD) — pas le mot de passe principal.
Doc : https://support.google.com/accounts/answer/185833
"""

import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

from config import (
    DATA_DIR,
    GMAIL_SMTP_PORT,
    GMAIL_SMTP_SERVER,
    WEEKLY_REPORT_RECIPIENT,
)
from history import formula_win_rate, posts_in_week

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
            lines.append(
                f"| {formula} | {stats['picked']} | {stats['avg_impressions']:,}".replace(",", " ") + " |"
            )
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
    """Génère le rapport de la semaine ISO précédente (lundi-dimanche)."""
    today = date.today()
    last_week_anchor = today - timedelta(days=7)
    year, week = _week_iso(last_week_anchor)

    posts = posts_in_week(year, week)
    winners = formula_win_rate(days=90)
    markdown = _render_markdown(year, week, posts, winners)

    report_path = REPORTS_DIR / f"linkedin-week-{year}-{week:02d}.md"
    report_path.write_text(markdown, encoding="utf-8")
    print(f"[weekly-report] saved to {report_path}", file=sys.stderr)

    subject = f"[LinkedIn] Rapport semaine {week:02d}/{year} — {len(posts)} posts"
    _send_email(subject, markdown, WEEKLY_REPORT_RECIPIENT)
    return report_path


if __name__ == "__main__":
    path = generate_for_last_week()
    print(path)
