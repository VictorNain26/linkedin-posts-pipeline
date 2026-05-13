"""
Format selector — décide entre carousel, text-only, poll.

Règle déterministe :
- Default = carousel (format core 2026 : 24% engagement, 3.5x reach).
- Si on a déjà eu MAX_SAME_FORMAT_STREAK carrousels consécutifs → switch
  vers text ou poll (alternance) pour varier (best practice 2026 :
  mix 1x/2sem).

Pas de LLM ici : règle pure, prévisible. La décision est loggée
dans format_history pour audit.
"""

import sys

from config import FORMAT_CAROUSEL, FORMAT_POLL, FORMAT_TEXT, MAX_SAME_FORMAT_STREAK
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


def _next_non_carousel(recent: list[str]) -> str:
    """Alterne text → poll → text quand on doit varier."""
    last_alt = next((f for f in recent if f != FORMAT_CAROUSEL), None)
    return FORMAT_POLL if last_alt == FORMAT_TEXT else FORMAT_TEXT


def select_format() -> tuple[str, str]:
    """Renvoie (format, reason). Enregistre la décision dans format_history."""
    recent = recent_formats(limit=MAX_SAME_FORMAT_STREAK + 2)
    streak = _carousel_streak(recent)
    if streak >= MAX_SAME_FORMAT_STREAK:
        decision = _next_non_carousel(recent)
        reason = f"{streak} carrousels d'affilée → switch vers {decision} (mix 2026)"
    else:
        decision = FORMAT_CAROUSEL
        reason = f"carrousel par défaut (streak actuel : {streak}/{MAX_SAME_FORMAT_STREAK})"
    record_format_decision(decision, reason)
    return decision, reason


if __name__ == "__main__":
    fmt, why = select_format()
    print(f"{fmt}\t{why}", file=sys.stderr)
    print(fmt)
