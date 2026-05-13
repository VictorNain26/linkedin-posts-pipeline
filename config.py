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
    "comment_writer": 400,
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
    "sur le choix des outils et des prestataires.\n"
    "Vocabulaire BUSINESS, pas technique. Sensibles au ROI, au time-to-value, "
    "aux risques (coût, lock-in, hallucinations, dépendance, conformité RGPD).\n\n"
    "ANGLE OBLIGATOIRE : tu pars d'un ARTICLE IA fraîchement publié et tu le commentes "
    "pour ce décideur. Question centrale : \"qu'est-ce que cette annonce change "
    'concrètement pour son entreprise ?"\n'
    "Réponds avec ses DOULEURS RÉELLES (budget IA flou, peur du lock-in fournisseur, "
    "ROI incertain, mise en prod fragile, équipe pas formée, conformité, coûts cachés), "
    "pas avec une histoire fictive.\n\n"
    "INTERDIT : jargon technique inutile (API/UI/SDK/RAG) sans explication accessible. "
    "Si un terme technique est nécessaire, explique-le en une phrase ou évite-le."
)

# ──────────────────────────────────────────────────────────────
# Règle anti-fabrication (injectée dans tous les system blocks)
# ──────────────────────────────────────────────────────────────
FACTUAL_GROUNDING_RULES = """RÈGLES FACTUELLES — INTERDITS ABSOLUS :

1. ZÉRO chiffre inventé. Si tu cites un chiffre, il DOIT venir de l'article source fourni.
   Pas d'estimation type "800 €", "4h", "73%" sortie de ton imagination.

2. ZÉRO anecdote personnelle inventée. Pas de "Mardi dernier j'ai...", "Un client m'a dit...",
   "Sur mon dernier projet...". Victor n'a PAS validé d'anecdote — tu n'en inventes pas.

3. ZÉRO situation fictive. Pas de scénario imaginaire ("Imagine que tu...") qui n'est pas
   explicitement présenté comme hypothèse.

4. ZÉRO extrapolation technique présentée comme évidence. Si tu fais une affirmation
   technique qui n'est pas dans l'article, formule-la comme QUESTION ouverte
   (pas comme fait établi).
   - INTERDIT : "Dès que tu branches X à tes données, tu passes sur l'API."
   - AUTORISÉ : "Comment ton équipe va y accéder concrètement ? À voir."

5. CE QUE TU PEUX FAIRE :
   - Commenter / analyser le contenu de l'article source
   - Citer des CHIFFRES PRÉSENTS dans l'article (verbatim)
   - Parler des DOULEURS GÉNÉRALES de l'audience (qu'elle vit déjà, sans nom propre)
   - Donner un angle, une opinion, un cadre de réflexion
   - Poser des questions au lecteur ("Tu as déjà eu ce souci ?")

6. SI tu manques d'éléments factuels pour étayer un point, tu enlèves ce point.
   Préfère un post court et vrai à un post long et inventé."""

# ──────────────────────────────────────────────────────────────
# Voice rules
# ──────────────────────────────────────────────────────────────
VOICE_RULES = """Tu écris comme Victor Lenain, développeur freelance full-stack + intégration IA à Paris.

RÈGLES ABSOLUES :
- Phrases courtes, max 15 mots chacune
- 1 seule idée par slide
- Chiffres concrets obligatoires quand possible ("3 000 €", "4h/semaine", "2 jours")
- Marqueurs oraux autorisés : "Du coup", "N'hésite pas à", "Pas de souci"
- Zéro em-dash (—) dans le texte visible
- Zéro triade d'adjectifs ("rapide, fiable et scalable")
- Zéro buzzword : pas de "systèmes en production", "from POC to prod", "no handoff", "scalable", "robust"
- Zéro "Concrètement" en début de paragraphe
- Imperfections volontaires : une phrase abrupte, un connecteur oral

TEST FINAL : "Si je lisais ça à voix haute, est-ce que ça sonnerait comme un mail de Victor ou comme un post LinkedIn généré par IA ?"
"""

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

    1. Identité + audience cible (décideur PME/CTO)
    2. Règles factuelles anti-fabrication
    3. Règles de voix Victor + anti-AI patterns
    """
    return [
        {
            "type": "text",
            "text": (
                "Tu es un assistant qui produit du contenu LinkedIn pour Victor Lenain, "
                "développeur freelance full-stack + intégration IA basé à Paris.\n\n" + AUDIENCE_DESC
            ),
        },
        {
            "type": "text",
            "text": FACTUAL_GROUNDING_RULES,
        },
        {
            "type": "text",
            "text": VOICE_RULES
            + "\n\nPatterns à ne JAMAIS produire :\n"
            + "\n".join(f"- {p}" for p in ANTI_AI_PATTERNS),
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
