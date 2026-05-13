"""
Configuration centrale du pipeline LinkedIn posts.

Architecture single-mode :
- 100% evergreen orienté prospect PME / CTO décisionnaire
- Pipeline ancré sur l'actualité IA (RSS) avec angle BUSINESS systématique
- Pas de mode "veille tech" — la cible n'est pas le dev curieux mais le décideur
"""

import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Models Anthropic
# ──────────────────────────────────────────────────────────────
SONNET_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

TOKEN_BUDGETS = {
    "rss_score": 300,
    "pain": 400,
    "angle": 300,
    "architect": 1200,
    "pen": 1500,
    "detector": 1500,
    "hook_generator": 800,
    "hook_judge": 300,
    "comment_writer": 500,  # +100 pour caser la citation source en plus du CTA
    "weekly_report": 2000,
}

# ──────────────────────────────────────────────────────────────
# Data paths (séparation code/data)
# ──────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("LINKEDIN_DATA_DIR", Path.home() / "linkedin-posts-data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "history.db"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = DATA_DIR / "logs"
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────
# Sources RSS — best practice 2026 : petit stack, signal haut
# Source : https://daige.st/en/blog/best-tech-rss-feeds-2026
# Audit : URLs vérifiées 2026-05-13, summaries OK sur les 3.
# ──────────────────────────────────────────────────────────────
RSS_SOURCES = [
    # Officiel OpenAI — modèle leader marché
    "https://openai.com/news/rss.xml",
    # Officiel Anthropic via community mirror (pas de feed officiel en 2026)
    "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
    # Pragmatic Engineer — adoption IA en équipes engineering (signal CTO)
    "https://newsletter.pragmaticengineer.com/feed",
]
RSS_FETCH_TIMEOUT = 10
RSS_LOOKBACK_HOURS = 48
RSS_ARTICLE_FETCH_TIMEOUT = 15
RSS_ARTICLE_MAX_CHARS = 4000

# ──────────────────────────────────────────────────────────────
# Audience cible — unique
# ──────────────────────────────────────────────────────────────
AUDIENCE_DESC = (
    "AUDIENCE : dirigeants de PME et CTOs français qui envisagent d'intégrer "
    "l'IA dans leur produit ou leurs process métier. Décideurs ou co-décideurs "
    "sur le choix des outils et des prestataires. La MAJORITÉ N'EST PAS TECHNIQUE.\n"
    "Vocabulaire BUSINESS, pas technique. Sensibles au ROI, au time-to-value, "
    "aux risques (coût, lock-in, hallucinations, dépendance, conformité RGPD).\n\n"
    "Angle : tu pars d'un article IA fraîchement publié et tu le commentes "
    "pour ce décideur. Question centrale : \"qu'est-ce que cette annonce change "
    'concrètement pour son entreprise ?"\n'
    "Réponds avec ses DOULEURS RÉELLES (budget IA flou, peur du lock-in fournisseur, "
    "ROI incertain, mise en prod fragile, équipe pas formée, conformité, coûts cachés), "
    "pas avec une histoire fictive.\n\n"
    "RÈGLE ANTI-JARGON (best practice LinkedIn B2B 2026) :\n"
    "- Si l'article cite un outil technique (Codex, SDK, API, RAG, MCP, etc.), "
    "  tu le mentionnes AU MAX 2 FOIS dans tout le post, et tu TRADUIS son rôle en métier.\n"
    "  À éviter : répéter 'Codex génère du code' 5 fois.\n"
    "  Préférable : 'Codex transforme tes fichiers Excel en automatisation' (1 fois) "
    "  puis 'l'outil fait X' / 'ça automatise Y' (paraphrases métier).\n"
    "- Pas de jargon anglo (MBRs, reporting packs, variance bridges) sans traduction FR "
    "  entre parenthèses la première fois.\n"
    "- Test : un PDG d'usine de 50 personnes peut-il lire chaque slide sans Google ? "
    "  Si non → reformule."
)

# ──────────────────────────────────────────────────────────────
# Règle anti-fabrication (injectée dans tous les system blocks)
# ──────────────────────────────────────────────────────────────
FACTUAL_GROUNDING_RULES = """<rules name="ancrage_factuel">

Règle 1 — Chiffres : tout chiffre cité doit venir de l'article source. Pas
d'estimation type "800 €", "4h", "73%" sortie de ton imagination.

Règle 2 — Anecdotes : pas d'anecdote personnelle inventée. Victor n'a pas validé
d'histoires de clients, projets ou retours terrain.
Exemples à éviter : "Mardi dernier j'ai...", "Un client m'a dit...",
"Sur mon dernier projet...".

Règle 3 — Situations fictives : pas de scénario imaginaire ("Imagine que tu...")
sauf s'il est explicitement présenté comme hypothèse.

Règle 4 — Extrapolations techniques : une affirmation technique qui n'est pas
dans l'article se formule en question ouverte plutôt qu'en fait établi.
- à éviter : "Dès que tu branches X à tes données, tu passes sur l'API."
- préférable : "Comment ton équipe va y accéder concrètement ? À voir."

Règle 5 — Opinions non marquées : une opinion ou affirmation hors article
(durée typique d'un projet, niveau de complexité, classement "le cas le plus X",
jugement de valeur) doit être :
  (a) préfixée par un marqueur d'opinion :
      "D'après ce que je vois sur le terrain...", "À mon avis...",
      "Sur les projets que je croise...", "Mon retour..."
  (b) transformée en question ouverte :
      "Pourquoi tant de projets IA finance traînent 6 mois ?"
  (c) supprimée si elle n'apporte pas de valeur cadrée.

  Exemples :
  - à éviter : "Ce n'est pas un projet de 6 mois."
  - préférable : "À mon avis, ce n'est pas un projet de 6 mois."
  - préférable : "Combien de temps pour automatiser ça ? Spoiler : moins que tu crois."

Règle 6 — Ce qui passe sans marquage :
  - Commenter / résumer / analyser le contenu de l'article (factuel sourcé)
  - Citer un chiffre présent dans l'article (verbatim)
  - Décrire les douleurs générales de l'audience (qu'elle vit déjà)
  - Poser des questions au lecteur ("Tu as déjà eu ce souci ?")
  - Donner un cadre de réflexion neutre ("3 questions à poser avant de te lancer")

Règle 7 — Coupe sans regret : si tu manques de fact ou d'opinion légitime pour
étayer un point, enlève-le. Un post court et vrai bat un post long et fabriqué.

</rules>"""

# ──────────────────────────────────────────────────────────────
# Voice rules
# ──────────────────────────────────────────────────────────────
VOICE_RULES = """<voice name="victor_lenain">

Tu écris comme Victor Lenain, développeur freelance full-stack + intégration IA
à Paris.

Style :
- Phrases courtes, max 15 mots
- 1 idée par slide
- Chiffres concrets quand possible ("3 000 €", "4h/semaine", "2 jours")
- Marqueurs oraux autorisés : "Du coup", "N'hésite pas à", "Pas de souci"

À éviter :
- em-dash (—) dans le texte visible
- triade d'adjectifs ("rapide, fiable et scalable")
- buzzword : "systèmes en production", "from POC to prod", "no handoff",
  "scalable", "robust"
- "Concrètement" en début de paragraphe

Préférences :
- Imperfections volontaires bienvenues (une phrase abrupte, un connecteur oral)

Test final avant de soumettre :
"Si je lisais ça à voix haute, est-ce que ça sonnerait comme un mail de Victor
ou comme un post LinkedIn généré par IA ?"

</voice>"""

ANTI_AI_PATTERNS = [
    "—",
    "Concrètement,",
    "notamment",
    "spécifiquement",
    "particulièrement",
    "systèmes en production",
    "from POC to prod",
    "no handoff",
    "sans handoff",
    "robuste",
    "scalable",
    "optimisé",
]


def system_voice() -> list[dict]:
    """System blocks (3 segments) — partagés par tous les agents.

    1. <role> identité + audience cible (décideur PME/CTO)
    2. <rules> règles factuelles anti-fabrication
    3. <voice> règles de style Victor + patterns à éviter

    Structure XML : les balises aident Claude 4.x à parser les sections
    (best practice Anthropic 2026).
    """
    return [
        {
            "type": "text",
            "text": (
                "<role>\n"
                "Tu es un assistant qui produit du contenu LinkedIn pour Victor Lenain, "
                "développeur freelance full-stack + intégration IA basé à Paris.\n\n"
                + AUDIENCE_DESC
                + "\n</role>"
            ),
        },
        {
            "type": "text",
            "text": FACTUAL_GROUNDING_RULES,
        },
        {
            "type": "text",
            "text": (
                VOICE_RULES
                + "\n\n<patterns_a_eviter>\n"
                + "\n".join(f"- {p}" for p in ANTI_AI_PATTERNS)
                + "\n</patterns_a_eviter>"
            ),
        },
    ]


# ──────────────────────────────────────────────────────────────
# Hashtags — unique set business prospect
# ──────────────────────────────────────────────────────────────
HASHTAGS = "#IntégrationIA #PME #IA #Productivité #Freelance"

# ──────────────────────────────────────────────────────────────
# CTA texte (tenable, pas de promesse d'article fantôme)
# ──────────────────────────────────────────────────────────────
CTA_SLIDE_TEXT = "Tu veux en discuter pour ton entreprise ? Mon DM est ouvert."
CTA_POST_SUFFIX = "💬 DM ouvert si tu veux en parler."

# ──────────────────────────────────────────────────────────────
# Formats de post LinkedIn
# ──────────────────────────────────────────────────────────────
FORMAT_CAROUSEL = "carousel"
FORMAT_TEXT = "text"
FORMAT_POLL = "poll"

# Rotation des formats — best practice 2026 : varier 1x/2sem.
# Default = carousel ; on switch text/poll après MAX_SAME_FORMAT_STREAK carrousels consécutifs.
MAX_SAME_FORMAT_STREAK = 3

# Carousel format (best practice 2026 : portrait 4:5).
SLIDE_COUNT_MIN = 5
SLIDE_COUNT_MAX = 10
SLIDE_COUNT_TARGET = 7

# ──────────────────────────────────────────────────────────────
# A/B hooks
# ──────────────────────────────────────────────────────────────
HOOK_VARIATIONS_COUNT = 3

# ──────────────────────────────────────────────────────────────
# 1er commentaire d'engagement (CTA)
# ──────────────────────────────────────────────────────────────
FIRST_COMMENT_DELAY_SECONDS = 30
PROFILE_URL = "https://victorlenain.fr"

# ──────────────────────────────────────────────────────────────
# LinkedIn API versioning
# ──────────────────────────────────────────────────────────────
LINKEDIN_API_VERSION = "202604"

# ──────────────────────────────────────────────────────────────
# Analytics & rapport hebdo
# ──────────────────────────────────────────────────────────────
ANALYTICS_LOOKBACK_DAYS = 30
WEEKLY_REPORT_RECIPIENT = os.environ.get("WEEKLY_REPORT_TO", "victor.lenain26@gmail.com")
GMAIL_SMTP_SERVER = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587

# ──────────────────────────────────────────────────────────────
# Pipeline tuning
# ──────────────────────────────────────────────────────────────
MAX_HISTORY_DAYS = 90
KEYWORD_OVERLAP_THRESHOLD = 0.4
MAX_DETECTOR_RETRIES = 2

# API resilience
ANTHROPIC_MAX_ATTEMPTS = 3
ANTHROPIC_RETRY_BASE_DELAY = 5
REQUESTS_TIMEOUT = 30
SQLITE_TIMEOUT = 10
