"""
Banque d'anecdotes réelles de Victor — la SEULE source autorisée pour le registre
"preuve" (retour terrain première personne).

Principe non négociable (cf. FACTUAL_GROUNDING_RULES) : le pipeline n'invente JAMAIS
d'histoire perso. Ce module charge des expériences vécues, écrites et validées par
Victor dans $LINKEDIN_DATA_DIR/state/victor_stories.json. Si le fichier est absent ou
vide, le registre "preuve" est simplement retiré de la rotation — pas de fabrication.

Format attendu (cf. victor_stories.example.json à la racine du repo) :
{
  "stories": [
    {
      "id": "slug-unique",
      "title": "Titre court de l'histoire",
      "context": "Qui, quoi, quand — 1-2 phrases",
      "facts": ["fait réel 1 (chiffres exacts)", "fait réel 2"],
      "lesson": "La leçon transférable pour la cible",
      "topics": ["mots-clés", "pour le matching d'angle"]
    }
  ]
}

Garde-fous :
- Fichier absent → [] (état légitime : banque pas encore remplie)
- JSON malformé → ValueError bruyante (un typo d'édition ne doit pas passer en silence)
- Story dont l'id commence par "EXEMPLE" → ignorée avec warning (placeholder du
  fichier example copié sans être édité — ne doit JAMAIS être publiée comme vécue)
"""

import json
import sys

from config import STORIES_PATH

_REQUIRED_KEYS = {"id", "title", "context", "facts", "lesson"}


def load_stories() -> list[dict]:
    """Charge les anecdotes validées. [] si la banque n'existe pas encore."""
    if not STORIES_PATH.exists():
        return []
    try:
        data = json.loads(STORIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"victor_stories.json malformé ({STORIES_PATH}) : {e}. "
            "Corrige le JSON — le pipeline ne publie pas avec une banque corrompue."
        ) from e
    stories = data.get("stories", [])
    valid: list[dict] = []
    for s in stories:
        if not isinstance(s, dict) or not _REQUIRED_KEYS.issubset(s.keys()):
            raise ValueError(
                f"Story invalide dans {STORIES_PATH} : chaque entrée requiert "
                f"{sorted(_REQUIRED_KEYS)}. Entrée fautive : {s!r}"
            )
        if str(s["id"]).upper().startswith("EXEMPLE"):
            print(
                f"[stories] story placeholder '{s['id']}' ignorée — "
                "édite victor_stories.json avec une VRAIE expérience",
                file=sys.stderr,
            )
            continue
        valid.append(s)
    return valid


def stories_index_block(stories: list[dict]) -> str:
    """Index compact (id + résumé) injecté au prompt de l'Angle Scout pour le choix."""
    lines = ["<victor_stories_index>"]
    lines.append(
        "Expériences RÉELLES de Victor, validées par lui. Choisis la plus pertinente "
        'pour l\'article (story_id), ou story_id="" si AUCUNE ne colle vraiment — '
        "ne force jamais un lien artificiel."
    )
    for s in stories:
        topics = ", ".join(s.get("topics", []))
        lines.append(f'<story id="{s["id"]}" topics="{topics}">{s["title"]} — {s["context"]}</story>')
    lines.append("</victor_stories_index>")
    return "\n".join(lines)


def story_block(story: dict) -> str:
    """Bloc complet d'UNE story, injecté aux agents créatifs + au factual check.
    Les facts listés ici deviennent du matériau factuel autorisé (cf. no-fake-anecdote)."""
    facts = "\n".join(f"- {f}" for f in story.get("facts", []))
    return (
        f'<victor_story id="{story["id"]}">\n'
        f"Titre : {story['title']}\n"
        f"Contexte : {story['context']}\n"
        f"Faits réels (seuls détails autorisés, à reprendre fidèlement) :\n{facts}\n"
        f"Leçon : {story['lesson']}\n"
        "</victor_story>"
    )


def get_story(stories: list[dict], story_id: str) -> dict | None:
    return next((s for s in stories if s["id"] == story_id), None)
