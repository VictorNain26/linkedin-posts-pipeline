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

from config import (
    FORMAT_CAROUSEL,
    FORMAT_TEXT,
    MAX_SAME_FORMAT_STREAK,
    REGISTRE_PREUVE,
    REGISTRES_ROTATION,
)
from history import recent_formats, recent_registres, record_format_decision


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


def select_registre(stories_available: bool) -> tuple[str, str]:
    """Registre éditorial du prochain post : least-recently-used parmi les registres
    disponibles (preuve retiré si la banque d'anecdotes est vide — on n'invente pas).

    Déterministe, pas de LLM. Premier post de l'historique → pedagogie
    (1er élément de REGISTRES_ROTATION).
    """
    options = [r for r in REGISTRES_ROTATION if r != REGISTRE_PREUVE or stories_available]
    recent = recent_registres(limit=10)

    def last_use(registre: str) -> int:
        # Index dans recent = ancienneté du dernier usage. Jamais utilisé → priorité max.
        try:
            return recent.index(registre)
        except ValueError:
            return len(recent) + 1

    choice = max(options, key=last_use)
    if not stories_available:
        reason = f"LRU parmi {options} (preuve sautée : victor_stories.json vide)"
    else:
        reason = f"LRU parmi {options}, derniers publiés : {recent[:3]}"
    return choice, reason


if __name__ == "__main__":
    fmt, why = select_format()
    print(f"{fmt}\t{why}", file=sys.stderr)
    print(fmt)
