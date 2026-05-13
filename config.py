"""
Configuration centrale du pipeline LinkedIn posts.

Architecture :
- 2 modes complémentaires : "evergreen" (mardi, cible PME) et "veille" (jeudi, cible devs).
- System blocks cacheables (cache_control ephemeral) partagés entre 6 agents par run.
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
    "hook_generator": 800,    # génère 3 variations en 1 call
    "hook_judge": 300,        # choisit la meilleure
    "comment_writer": 400,    # 1er commentaire d'engagement
    "format_picker": 200,     # facultatif : decide_format via LLM (sinon règle)
    "weekly_report": 2000,    # synthèse rapport hebdo
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
RSS_SOURCES = [
    "https://www.anthropic.com/rss.xml",
    "https://openai.com/news/rss/",
    "https://huggingface.co/blog/feed.xml",
    "https://tldr.tech/api/rss/ai",
    "https://www.lemondeinformatique.fr/flux-rss/thematique-intelligence-artificielle-107.xml",
]
RSS_FETCH_TIMEOUT = 10
RSS_LOOKBACK_HOURS = 48

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
        "Cible : dirigeants de PME et CTOs français qui envisagent d'intégrer l'IA "
        "dans leur produit ou leurs process métier.\n"
        "Vocabulaire business, pas technique. Sensibles au ROI, au time-to-value, "
        "aux risques (coût, lock-in, hallucinations).\n\n"
        "MISSION : tu pars d'une news IA fraîche fournie par l'utilisateur et tu en tires "
        "un angle BUSINESS PROSPECT — \"voici ce que cette annonce change concrètement pour "
        "un dirigeant de PME\". Pas de jargon technique. Toujours ramener à un enjeu concret "
        "(coût, productivité, risque, opportunité commerciale)."
    ),
    MODE_VEILLE: (
        "Cible : développeurs, tech leads et CTOs qui implémentent de l'IA.\n"
        "Vocabulaire technique OK (SDK, LLM, RAG, agents, MCP), retours d'expérience terrain. "
        "Sensibles au gain de productivité, aux pièges, aux comparaisons franches d'outils.\n\n"
        "MISSION : tu pars d'une news IA fraîche fournie par l'utilisateur et tu en tires "
        "un angle DEV/TECH — \"voici ce que cette annonce change concrètement pour quelqu'un "
        "qui code des intégrations IA\". Retour terrain, comparaisons honnêtes, "
        "patterns concrets, pas de hype."
    ),
}

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
    """System blocks par mode — 2 cache breakpoints stables."""
    return [
        {
            "type": "text",
            "text": (
                "Tu es un assistant qui produit du contenu LinkedIn pour Victor Lenain, "
                "développeur freelance full-stack + intégration IA basé à Paris.\n\n"
                + AUDIENCE_DESC[mode]
            ),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": VOICE_RULES + "\n\nPatterns à ne JAMAIS produire :\n" + "\n".join(f"- {p}" for p in ANTI_AI_PATTERNS),
            "cache_control": {"type": "ephemeral"},
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

# Stratégie de rotation des formats (best practice 2026 : varier 1×/2sem)
# - evergreen (mardi) : toujours carousel — format core pour PME prospects
# - veille (jeudi)    : carousel par défaut, mais on switch text/poll
#   si >= MAX_SAME_FORMAT_STREAK carousels veille publiés à la suite.
MAX_SAME_FORMAT_STREAK = 3

# Carousel format (best practice 2026 : portrait 4:5).
# Dimensions effectives en dur dans html_to_pdf.js (1080x1350) — pas exposées
# côté Python car aucun module Python ne les consomme.
SLIDE_COUNT = 7

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
