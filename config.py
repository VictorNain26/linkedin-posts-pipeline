"""
Configuration centrale du pipeline LinkedIn posts.

Architecture :
- 2 modes complémentaires : "evergreen" (mardi, cible PME) et "veille" (jeudi, cible devs).
- System blocks partagés entre 8 agents par run (caching documenté dans system_voice).
- Token budgets explicites par agent (évite de payer pour des max_tokens non utilisés).
"""

import datetime
import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Models
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
    "hook_generator": 800,  # génère 3 variations en 1 call
    "hook_judge": 300,  # choisit la meilleure
    "comment_writer": 400,  # 1er commentaire d'engagement
    "format_picker": 200,  # facultatif : decide_format via LLM (sinon règle)
    "weekly_report": 2000,  # synthèse rapport hebdo
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
# Sources RSS (veille)
# ──────────────────────────────────────────────────────────────
# URLs vérifiées en mai 2026. Audit régulier nécessaire car les feeds bougent.
# - Anthropic n'a pas de feed officiel : on utilise un community mirror (à monitorer).
RSS_SOURCES = [
    # Officiels (avec summary/description) :
    "https://openai.com/news/rss.xml",
    "https://www.lemondeinformatique.fr/flux-rss/intelligence-artificielle/rss.xml",
    # Community mirror (Anthropic n'a pas de feed officiel en 2026) :
    "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
    # Titres only (summary vide) — on WebFetch leur URL pour le content :
    "https://huggingface.co/blog/feed.xml",
    "https://tldr.tech/api/rss/ai",
]
RSS_FETCH_TIMEOUT = 10
RSS_LOOKBACK_HOURS = 48
RSS_ARTICLE_FETCH_TIMEOUT = 15  # WebFetch sur les URLs articles
RSS_ARTICLE_MAX_CHARS = 4000  # cap pour éviter blowup tokens

# ──────────────────────────────────────────────────────────────
# Modes : evergreen (mardi, PME) vs veille (jeudi, devs)
# ──────────────────────────────────────────────────────────────
MODE_EVERGREEN = "evergreen"
MODE_VEILLE = "veille"

# Mardi = 1, Jeudi = 3 (weekday ISO)
MODE_BY_WEEKDAY = {
    1: MODE_EVERGREEN,
    3: MODE_VEILLE,
}


def current_mode(override: str | None = None) -> str:
    """Choisit le mode selon le weekday, ou via override env / arg."""
    if override:
        return override
    env_mode = os.environ.get("PIPELINE_MODE")
    if env_mode:
        return env_mode
    return MODE_BY_WEEKDAY.get(datetime.date.today().weekday(), MODE_EVERGREEN)


# ──────────────────────────────────────────────────────────────
# Audience par mode (utilisé dans les system blocks)
# ──────────────────────────────────────────────────────────────
AUDIENCE_DESC = {
    MODE_EVERGREEN: (
        "AUDIENCE : dirigeants de PME et CTOs français qui envisagent d'intégrer l'IA "
        "dans leur produit ou leurs process métier.\n"
        "Vocabulaire BUSINESS, pas technique. Sensibles au ROI, au time-to-value, "
        "aux risques (coût, lock-in, hallucinations, dépendance).\n\n"
        "ANGLE : tu pars d'un ARTICLE IA fraîchement publié et tu commentes pour ce dirigeant. "
        "Question centrale : \"qu'est-ce que cette annonce change concrètement pour lui ?\"\n"
        "Réponds avec ses DOULEURS RÉELLES (budget IA flou, peur du lock-in fournisseur, "
        "ROI incertain, mise en prod fragile, équipe pas formée), pas avec une histoire fictive."
    ),
    MODE_VEILLE: (
        "AUDIENCE : développeurs, tech leads et CTOs qui implémentent de l'IA dans des apps web.\n"
        "Vocabulaire technique OK (SDK, LLM, RAG, agents, MCP, tool use). "
        "Sensibles au gain de productivité, aux pièges d'intégration, aux comparaisons d'outils.\n\n"
        "ANGLE : tu pars d'un ARTICLE IA fraîchement publié et tu commentes pour ce dev. "
        "Question centrale : \"qu'est-ce que cette annonce change pour quelqu'un qui code "
        "des intégrations IA aujourd'hui ?\"\n"
        "Réponds avec ses DOULEURS RÉELLES (choix de stack, intégration fragile, doc obsolète, "
        "modèles incompatibles, coûts API qui dérivent), pas avec une histoire fictive."
    ),
}

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

4. CE QUE TU PEUX FAIRE :
   - Commenter / analyser le contenu de l'article source
   - Citer des CHIFFRES PRÉSENTS dans l'article (verbatim)
   - Parler des DOULEURS GÉNÉRALES de l'audience (qu'elle vit déjà, sans nom propre)
   - Donner un angle, une opinion, un cadre de réflexion
   - Poser des questions au lecteur ("Tu as déjà eu ce souci ?")

5. SI tu manques d'éléments factuels pour étayer un point, tu enlèves ce point.
   Préfère un post court et vrai à un post long et inventé."""

# ──────────────────────────────────────────────────────────────
# Voice rules (constant, cacheable)
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


def system_voice(mode: str) -> list[dict]:
    """System blocks par mode.

    Structure :
    - Bloc 1 : identité + audience + angle prospect
    - Bloc 2 : règles factuelles anti-fabrication
    - Bloc 3 : règles de voix Victor + anti-AI patterns
    """
    return [
        {
            "type": "text",
            "text": (
                "Tu es un assistant qui produit du contenu LinkedIn pour Victor Lenain, "
                "développeur freelance full-stack + intégration IA basé à Paris.\n\n"
                + AUDIENCE_DESC[mode]
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
# Hashtags par mode (3-5 hyper-ciblés, best practice 2026)
# ──────────────────────────────────────────────────────────────
HASHTAGS_BY_MODE = {
    MODE_EVERGREEN: "#IntégrationIA #PME #IA #Productivité #Freelance",
    MODE_VEILLE: "#IA #LLM #Claude #IntégrationIA #DevFreelance",
}

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

# Stratégie de rotation des formats (best practice 2026 : varier 1x/2sem)
# - evergreen (mardi) : toujours carousel — format core pour PME prospects
# - veille (jeudi)    : carousel par défaut, mais on switch text/poll
#   si >= MAX_SAME_FORMAT_STREAK carousels veille publiés à la suite.
MAX_SAME_FORMAT_STREAK = 3

# Carousel format (best practice 2026 : portrait 4:5).
# Dimensions effectives en dur dans html_to_pdf.js (1080x1350) — pas exposées
# côté Python car aucun module Python ne les consomme.
# Slide count variable : l'agent Architect décide selon le besoin du contenu.
SLIDE_COUNT_MIN = 5
SLIDE_COUNT_MAX = 10
SLIDE_COUNT_TARGET = 7  # sweet spot 2026 (référence pour le prompt)

# ──────────────────────────────────────────────────────────────
# Pipeline tuning
# ──────────────────────────────────────────────────────────────
MAX_HISTORY_DAYS = 90
KEYWORD_OVERLAP_THRESHOLD = 0.4
MAX_DETECTOR_RETRIES = 2

# A/B hooks
HOOK_VARIATIONS_COUNT = 3  # contrarian + data + narrative

# 1er commentaire d'engagement
FIRST_COMMENT_DELAY_SECONDS = 30  # delay après le post (paraître naturel)
PROFILE_URL = "https://victorlenain.fr"

# LinkedIn API versioning
LINKEDIN_API_VERSION = "202604"  # bump tous les ~3 mois (cf. Microsoft Learn)

# Analytics & rapport hebdo
ANALYTICS_LOOKBACK_DAYS = 30
WEEKLY_REPORT_RECIPIENT = os.environ.get("WEEKLY_REPORT_TO", "victor.lenain26@gmail.com")
GMAIL_SMTP_SERVER = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587

# API resilience
ANTHROPIC_MAX_ATTEMPTS = 3
ANTHROPIC_RETRY_BASE_DELAY = 5
REQUESTS_TIMEOUT = 30
SQLITE_TIMEOUT = 10
