"""
Injection des learnings hebdo (data-driven) dans le system block des agents.

Workflow :
1. `weekly_report.py` (lundi 7h) génère `state/learnings.json` via Claude Sonnet
2. À chaque appel d'agent, `_system_with_learnings()` charge ce JSON et ajoute un
   4e block au system avec `cache_control: ephemeral` → -90% coût/latence sur appels répétés

Garde-fous (rétrocompatibilité + anti-drift) :
- learnings.json absent / malformé / vide → fallback transparent (pipeline tourne comme avant)
- learnings.json > 14j → ignoré (stale, force régénération hebdo)
- MAX 5 biases injectés (cap dans le schema tool_use côté generate_learnings)
- Les biases sont des HINTS doux. Les règles statiques (factual_grounding, voice,
  anti_patterns) prévalent toujours en cas de conflit.
"""

import json
from datetime import datetime, timedelta

from config import LEARNINGS_PATH, system_voice


def _load_learnings_block() -> dict | None:
    """Charge state/learnings.json si présent et valide. Retourne le 4e system block
    avec cache_control: ephemeral, ou None si pas de learnings (fallback transparent).

    Garde-fous :
    - Si fichier absent / malformé → None (pipeline tourne comme avant, rétrocompat)
    - Si learnings.json vide ou biases=[] → None
    - Si learnings > 14 jours (stale) → None (force regen)
    """
    if not LEARNINGS_PATH.exists():
        return None
    try:
        data = json.loads(LEARNINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    biases = data.get("biases", [])
    if not biases:
        return None
    # Anti-staleness : > 14j → ignore
    try:
        gen_at = datetime.fromisoformat(data.get("generated_at", "1970-01-01"))
        if datetime.now() - gen_at > timedelta(days=14):
            return None
    except (ValueError, TypeError):
        pass

    # Construit un block XML compact (limite 5 biases × ~30 tokens = ~150 tokens)
    lines = ["<past_learnings>"]
    lines.append(
        f'<context generated_at="{data.get("generated_at", "?")}" '
        f'based_on_posts="{data.get("based_on_posts", "?")}">'
    )
    lines.append(data.get("summary", "")[:400])
    lines.append("</context>")
    lines.append("<biases_to_apply>")
    for b in biases[:5]:  # safety cap
        lines.append(
            f'  <bias type="{b.get("type", "?")}" key="{b.get("key", "?")}">'
            f'{b.get("instruction", "")}</bias>'
        )
    lines.append("</biases_to_apply>")
    lines.append(
        "<usage>Ces learnings sont des BIAS DOUX. Ils orientent tes choix mais "
        "ne remplacent JAMAIS les règles statiques (factual grounding, voice, anti-patterns). "
        "En cas de conflit, les règles statiques prévalent.</usage>"
    )
    lines.append("</past_learnings>")
    return {
        "type": "text",
        "text": "\n".join(lines),
        "cache_control": {"type": "ephemeral"},
    }


def _system_with_learnings(model: str | None = None) -> list[dict]:
    """system_voice(model) étendu d'un bloc learnings si disponibles.

    Utilisé comme `system=` dans tous les appels d'agents (sauf scoring RSS qui a
    son propre system block dédié au scoring).
    Passer model=HAIKU_MODEL active le bloc fusionné (seuil cache 2048 tokens).
    """
    blocks = system_voice(model)
    learn = _load_learnings_block()
    if learn is not None:
        blocks.append(learn)
    return blocks
