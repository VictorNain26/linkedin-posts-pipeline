"""
Format selector — décide entre carousel et text-only.

Best practice 2026 actualisée :
- Carousel (PDF) = format roi : 6.6% engagement, +278% vs text-only (Dataslayer, Buffer 2026).
- Text long contrarian = bonne variation après une série de carrousels.
- Polls RETIRÉS du roulement : 1.78x reach MAIS 0.37x engagement (reach trap qui kill
  l'algo et fait baisser la reach des posts suivants).
  Sources : Richard van der Blom Algorithm InSights 2025, Dataslayer Feb 2026,
  ConnectSafely engagement-pods report.

Règle déterministe :
- Default = carousel.
- Switch vers text-only après MAX_SAME_FORMAT_STREAK carrousels consécutifs.

Pas de LLM ici : règle pure, prévisible. La décision est loggée dans format_history.
"""

import sys

from config import FORMAT_CAROUSEL, FORMAT_TEXT, MAX_SAME_FORMAT_STREAK
from history import recent_formats, record_format_decision


def _carousel_streak(formats: list[str]) -> int:
    """Compte les carrousels consécutifs en tête (du plus récent au plus ancien)."""
    streak = 0
    for f in formats:
        if f == FORMAT_CAROUSEL:
            streak += 1
        else:
            break
    return streak


def select_format() -> tuple[str, str]:
    """Renvoie (format, reason). Enregistre la décision dans format_history."""
    recent = recent_formats(limit=MAX_SAME_FORMAT_STREAK + 2)
    streak = _carousel_streak(recent)
    if streak >= MAX_SAME_FORMAT_STREAK:
        decision = FORMAT_TEXT
        reason = f"{streak} carrousels d'affilée → switch vers text-only (variation 2026)"
    else:
        decision = FORMAT_CAROUSEL
        reason = f"carrousel par défaut (streak actuel : {streak}/{MAX_SAME_FORMAT_STREAK})"
    record_format_decision(decision, reason)
    return decision, reason


if __name__ == "__main__":
    fmt, why = select_format()
    print(f"{fmt}\t{why}", file=sys.stderr)
    print(fmt)
