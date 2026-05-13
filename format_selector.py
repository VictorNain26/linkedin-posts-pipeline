"""
Format selector — décide entre carousel, text-only, poll.

Règles (déterministes, traçables) :
- Mode evergreen (mardi) : toujours carousel (format core pour PME prospects).
- Mode veille (jeudi)    : carousel par défaut, mais switch text/poll
  si on a déjà eu MAX_SAME_FORMAT_STREAK carousels veille à la suite.
  Alterne text puis poll pour varier.

Pas de LLM ici : règle pure, prévisible. La décision est loggée
dans format_history pour audit.
"""

import sys

from config import (
    FORMAT_CAROUSEL,
    FORMAT_POLL,
    FORMAT_TEXT,
    MAX_SAME_FORMAT_STREAK,
    MODE_EVERGREEN,
    MODE_VEILLE,
)
from history import recent_formats_for_mode, record_format_decision


def _carousel_streak(formats: list[str]) -> int:
    """Compte les carrousels consécutifs en tête (plus récents)."""
    streak = 0
    for f in formats:
        if f == FORMAT_CAROUSEL:
            streak += 1
        else:
            break
    return streak


def _next_non_carousel(recent_formats: list[str]) -> str:
    """Alterne text → poll → text quand on doit varier."""
    last_alt = next((f for f in recent_formats if f != FORMAT_CAROUSEL), None)
    if last_alt == FORMAT_TEXT:
        return FORMAT_POLL
    return FORMAT_TEXT


def select_format(mode: str) -> tuple[str, str]:
    """Renvoie (format, reason). Enregistre la décision dans format_history."""
    if mode == MODE_EVERGREEN:
        decision = FORMAT_CAROUSEL
        reason = "evergreen mode → always carousel (core format for PME prospects)"
        record_format_decision(mode, decision, reason)
        return decision, reason

    if mode == MODE_VEILLE:
        recent = recent_formats_for_mode(mode, limit=MAX_SAME_FORMAT_STREAK + 2)
        streak = _carousel_streak(recent)
        if streak >= MAX_SAME_FORMAT_STREAK:
            decision = _next_non_carousel(recent)
            reason = f"veille mode → {streak} carousels in a row, switching to {decision} (format mix best practice 2026)"
        else:
            decision = FORMAT_CAROUSEL
            reason = f"veille mode → carousel default (current streak: {streak}/{MAX_SAME_FORMAT_STREAK})"
        record_format_decision(mode, decision, reason)
        return decision, reason

    raise ValueError(f"Unknown mode: {mode}")


if __name__ == "__main__":
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not mode_arg:
        print("Usage: python format_selector.py <evergreen|veille>", file=sys.stderr)
        sys.exit(2)
    fmt, why = select_format(mode_arg)
    print(f"{fmt}\t{why}")
