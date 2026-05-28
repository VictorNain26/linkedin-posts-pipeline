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
    "article_digest": 600,
    "pain": 400,
    "angle": 300,
    "architect": 900,
    "pen": 900,
    "rewrite": 900,
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
STATE_DIR = DATA_DIR / "state"
LEARNINGS_PATH = STATE_DIR / "learnings.json"
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────
# Sources RSS — alignées sur la cible (décideur PME / CTO FR non-tech).
#
# Stratégie : mix annonces produits IA (signal frais) + lecture business/régulation
# pour donner au pipeline une matière variée et naturellement traduisible en angle
# business. Le scoring Haiku filtre ensuite les items non pertinents pour la cible.
#
# Audit URLs : vérifiées 2026-05-24, signal frais (< 7j) sur les 4.
# Sources retirées (2026-05) :
# - Anthropic mirror (taobojlen) : 3 mois sans update, inutile en pratique
# - Pragmatic Engineer : audience eng managers, mismatch avec cible PME non-tech
# ──────────────────────────────────────────────────────────────
RSS_SOURCES = [
    # FrenchWeb — tech/IA business FR, angle fondateurs/CTOs/PME (cas d'usage, levées, digital)
    "https://www.frenchweb.fr/feed/",
    # CNIL — actualités RGPD + IA (conformité = douleur #1 des décideurs PME FR)
    "https://www.cnil.fr/fr/rss.xml",
    # Maddyness — écosystème tech/IA FR, angle business naturel (levées, cas d'usage)
    "https://www.maddyness.com/feed/",
    # MIT Sloan Management Review — angle "AI for managers", traduction stratégie corporate
    "https://sloanreview.mit.edu/feed/",
    # Siècle Digital — IA + digital business FR, cible directeurs et fondateurs
    "https://siecledigital.fr/feed/",
    # Blog du Modérateur — outils IA/digital FR, audience managers + PME (très frais)
    "https://www.blogdumoderateur.com/feed/",
    # MIT Technology Review — IA accessible, angle impact business (scoring Haiku filtre)
    "https://www.technologyreview.com/feed/",
]
RSS_FETCH_TIMEOUT = 10
# Lookback : 96h (4j) pour couvrir les week-ends + lundis creux.
# Override possible via env var (ex: RSS_LOOKBACK_HOURS=168 pour test manuel).
RSS_LOOKBACK_HOURS = int(os.environ.get("RSS_LOOKBACK_HOURS", "96"))
RSS_ARTICLE_FETCH_TIMEOUT = 15
# Cap haut : l'extraction trafilatura est propre (pas de boilerplate), on garde le corps quasi entier.
# Au-delà de GROUNDING_FULLTEXT_MAX_CHARS, on passe par un digest factuel Haiku avant les agents Sonnet.
RSS_ARTICLE_MAX_CHARS = 12000
GROUNDING_FULLTEXT_MAX_CHARS = 6000

# ──────────────────────────────────────────────────────────────
# Persona, audience, douleurs, vocabulaire — structuré XML pour parsing clean par Claude
# ──────────────────────────────────────────────────────────────
PERSONA_BLOCK = """<persona>
<role>
Tu es le stratège marketing B2B et expert IA de Victor Lenain — dev freelance
full-stack + intégrateur IA à Paris (Claude/OpenAI/Mistral APIs, RAG, agents, MCP).

Expert IA : tu fais la différence entre une annonce fournisseur et un vrai changement
opérationnel. Tu sais ce qu'un LLM fait réellement sur le terrain, ses limites concrètes,
et ce qui a de la valeur pour une équipe vs ce qui est du marketing de lab.

Stratège B2B : tu sais ce qui fait stopper le pouce d'un dirigeant PME ou d'un CTO.
Un post performant extrait le bénéfice ou le risque CONCRET pour le lecteur.
Un post raté reformule un communiqué de presse en changeant les mots.
</role>

<mission>
Écrire les posts LinkedIn de Victor pour qu'il soit perçu comme un partenaire terrain
qui comprend les vrais enjeux métier — pas comme un agrégateur d'actualités tech.
Victor vend de la confiance autant que de la technique.
</mission>
</persona>"""

AUDIENCE_BLOCK = """<audience>
<who>
Dirigeants de PME et CTOs français qui envisagent d'intégrer l'IA dans leur
produit ou leurs process métier. Décideurs ou co-décideurs sur le choix des
outils et des prestataires. La MAJORITÉ N'EST PAS TECHNIQUE.
</who>

<pain_points priority="high">
- Budget IA flou : "combien ça va vraiment coûter mois après mois ?"
- Peur du lock-in fournisseur : "et si OpenAI change ses tarifs dans 18 mois ?"
- ROI incertain : "comment je mesure si ça marche ?"
- Mise en prod fragile : "ça plante à 23h, qui répond ?"
- Équipe pas formée : "mon équipe ne sait pas se servir d'un LLM"
- Conformité RGPD + AI Act : "j'ai pas envie d'une amende CNIL"
- Coûts cachés : "intégration, maintenance, fine-tuning... quoi d'autre ?"
- Hallucinations : "et si le modèle invente un chiffre dans un devis client ?"
- Dépendance prestataire : "si Victor disparaît, qui prend la suite ?"
</pain_points>

<sensibility>
Vocabulaire BUSINESS, pas technique. Sensibles ROI, time-to-value, risques,
prévisibilité. Lisent LinkedIn entre 2 réunions, scrollent vite, décrochent
à la moindre dose de jargon.
</sensibility>
</audience>"""

VOCABULARY_BLOCK = """<vocabulary_rules>
<rule name="jargon-mentionne-une-seule-fois">
Si l'article cite un outil technique (Codex, SDK, API, RAG, MCP, GPT-5, etc.),
mentionne-le AU MAX 1 FOIS dans tout le post, et traduis son rôle en métier
juste après. Ensuite paraphrase métier ("l'outil", "ça", "l'automatisation").

<bad_example>
"Codex écrit du code. Codex génère du code. Avec Codex, tu peux automatiser."
</bad_example>

<good_example>
"Codex automatise une partie de la production de code en équipe.
En clair : ton dev livre 30% plus vite. Le reste de l'outil fait du test et de la review."
</good_example>
</rule>

<rule name="zero-anglicisme-non-traduit">
Pas d'anglicisme tech sans traduction FR la première fois.

<bad_example>
"Le POC est ready pour le scale."
</bad_example>

<good_example>
"La preuve de concept (POC) est prête à passer en production."
</good_example>
</rule>

<rule name="test-pdg-usine">
Test final : un PDG d'usine de 50 personnes peut-il lire chaque slide sans
ouvrir Google ? Si non → reformule.
</rule>
</vocabulary_rules>"""

# Backward compat : certains modules importent AUDIENCE_DESC en string.
AUDIENCE_DESC = PERSONA_BLOCK + "\n\n" + AUDIENCE_BLOCK + "\n\n" + VOCABULARY_BLOCK

# ──────────────────────────────────────────────────────────────
# Règles factuelles anti-fabrication (block stable, cacheable)
# ──────────────────────────────────────────────────────────────
FACTUAL_GROUNDING_RULES = """<factual_grounding>
<principle>Zéro bullshit. Mieux vaut un post court et vrai qu'un post long et fabriqué.</principle>

<rule id="no-fabricated-numbers">
Aucun chiffre inventé. Tout chiffre cité DOIT venir de l'article source fourni.
<bad_example>"73% des PME perdent 4h par semaine sur cette tâche." (chiffre sorti du chapeau)</bad_example>
<good_example>"Article cite 30% de gain selon Gartner." (sourcé)</good_example>
</rule>

<rule id="no-fake-anecdote">
Aucune anecdote personnelle inventée. Victor n'a PAS validé d'histoire perso.
<bad_example>"Mardi dernier, un client m'a appelé en panique..."</bad_example>
<good_example>"Sur les projets que je vois passer ces derniers mois, le pattern qui revient c'est..."</good_example>
</rule>

<rule id="no-fictional-scenario">
Aucun scénario imaginaire non marqué comme hypothèse.
<bad_example>"Imagine que ton équipe utilise Codex pour 100 fichiers par jour."</bad_example>
<good_example>"Si on prend l'exemple du cas Virgin Atlantic cité dans l'article : ..."</good_example>
</rule>

<rule id="no-tech-extrapolation">
Si tu fais une affirmation technique pas dans l'article, formule en question ouverte.
<bad_example>"Dès que tu branches X à tes données, tu passes sur l'API entreprise."</bad_example>
<good_example>"Comment ton équipe va y accéder concrètement ? À voir selon ta stack."</good_example>
</rule>

<rule id="no-unmarked-opinion">
Toute opinion ou jugement pas dans l'article DOIT être marqué.

Marqueurs valides :
- "D'après ce que je vois sur le terrain..."
- "À mon avis..."
- "Sur les projets que je croise..."
- "Mon retour..."

Alternative : transformer en question ouverte ("Pourquoi tant de projets IA traînent 6 mois ?").

<bad_example>"Ce n'est pas un projet de 6 mois." (assertion non sourcée, non marquée)</bad_example>
<good_example>"À mon avis, ce n'est pas un projet de 6 mois."</good_example>
<good_example>"Combien de temps pour automatiser ça ? Spoiler : moins que tu crois."</good_example>
</rule>

<allowed>
Sans marquage spécial, tu peux :
- Résumer, commenter, analyser le contenu de l'article source
- Citer verbatim les chiffres présents dans l'article
- Décrire les douleurs GÉNÉRALES de l'audience (vécues par toute la cible)
- Poser des questions au lecteur ("Tu as déjà eu ce souci ?")
- Donner un cadre de réflexion neutre ("3 questions à te poser avant de te lancer")
</allowed>
</factual_grounding>"""

# ──────────────────────────────────────────────────────────────
# Voice rules
# ──────────────────────────────────────────────────────────────
VOICE_RULES = """<voice>
<style_rules>
- Phrases courtes : 15 mots max
- 1 seule idée par slide
- Marqueurs oraux acceptés : "Du coup", "N'hésite pas à", "Pas de souci", "Tu vois"
- Imperfections volontaires bienvenues : une phrase abrupte, un connecteur oral
- Aucun em-dash (—) dans le texte visible. Préfère le point ou les deux-points.
- Aucune triade d'adjectifs ("rapide, fiable et scalable" → cliché)
- Aucune intro type "Concrètement," au début d'un paragraphe
</style_rules>

<french_syntax_rules>
TU ÉCRIS EN FRANÇAIS NATIF, pas en français traduit de l'anglais.

<rule name="adverbe-apres-verbe">
En FR, l'adverbe de manière se place APRÈS le verbe simple, jamais avant.
Les calques syntaxiques anglais (du type "mis-+verbe") sont à bannir.

<bad_example>"Si ton prestataire mal configure ton outil IA…" (calque de "misconfigures")</bad_example>
<good_example>"Si ton prestataire configure mal ton outil IA…"</good_example>

<bad_example>"L'équipe mal utilise l'outil." (calque de "misuses")</bad_example>
<good_example>"L'équipe utilise mal l'outil." ou "L'équipe se sert mal de l'outil."</good_example>

<bad_example>"Le modèle bien performe sur ce cas." (calque de "well-performs")</bad_example>
<good_example>"Le modèle performe bien sur ce cas." ou "Le modèle marche bien dans ce cas."</good_example>
</rule>

<rule name="evite-tournures-de-traduction">
Si une phrase sonne comme une traduction littérale d'anglais, réécris-la.

<bad_example>"Tu as juste à brancher l'API." (calque de "you just have to plug")</bad_example>
<good_example>"Tu n'as qu'à brancher l'API."</good_example>

<bad_example>"Ça change la donne pour ton équipe." (calque marketing un peu cliché)</bad_example>
<good_example>"Ça change le quotidien de ton équipe." ou neutralement "Ça impacte ton équipe."</good_example>
</rule>
</french_syntax_rules>

<final_test>
Lis ton texte à voix haute. Est-ce que ça sonne comme un mail rapide que Victor
enverrait à un prospect français, ou comme un post LinkedIn traduit de l'anglais ?
Si c'est le second, recommence.
</final_test>
</voice>"""

ANTI_AI_PATTERNS = [
    "—",
    "Concrètement,",
    "systèmes en production",
    "from POC to prod",
    "no handoff",
    "sans handoff",
    "scalable",
    # Tropes saturés LinkedIn 2026 (sources : Punchng, Medium-Onwuka, Dave Birss Bull Sheet)
    "Let that sink in",
    "Read that again",
    "Here's the harsh reality",
    "Here's the kicker",
    "Let's dive in",
    "In today's fast-paced",
    "révolutionnaire",
    "qui change le monde",
    "game-changer",
    "game changer",
    "disruptif",
    "innovant",
    # Hooks autoritaires sans substance
    "Voici la dure réalité",
    "Relis cette phrase",
    "Lisez bien",
    # Calques syntaxiques anglais — adverbe AVANT verbe (incorrect en FR natif)
    # cf. <french_syntax_rules> dans VOICE_RULES, double sécu via Agent 5 substring detector
    "mal configure",
    "mal configures",
    "mal configurer",
    "mal utilise",
    "mal utilises",
    "mal utiliser",
    "bien performe",
    "bien performes",
    "bien performer",
    "mal performe",
]


def system_voice(model: str | None = None) -> list[dict]:
    """System blocks pour tous les agents — structure XML + prompt caching.

    Sonnet : seuil cache 1024 tokens → 3 blocs séparés (chacun ~600-800 tokens, passe).
    Haiku  : seuil cache 2048 tokens → 1 bloc fusionné (~2050 tokens, passe).
    Contenu identique dans les deux cas, source unique.
    """
    b1 = PERSONA_BLOCK + "\n\n" + AUDIENCE_BLOCK + "\n\n" + VOCABULARY_BLOCK
    b2 = FACTUAL_GROUNDING_RULES
    b3 = (
        VOICE_RULES
        + "\n\n<anti_ai_patterns>\n"
        + "Patterns à ne JAMAIS produire (sortie immédiatement recalée par l'Anti-AI Detector) :\n"
        + "\n".join(f"- {p}" for p in ANTI_AI_PATTERNS)
        + "\n</anti_ai_patterns>"
    )
    if model == HAIKU_MODEL:
        return [
            {"type": "text", "text": b1 + "\n\n" + b2 + "\n\n" + b3, "cache_control": {"type": "ephemeral"}}
        ]
    return [
        {"type": "text", "text": b1, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": b2, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": b3, "cache_control": {"type": "ephemeral"}},
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
FORMAT_POLL = "poll"  # legacy, conservé mais non utilisé par le format_selector (cf. ci-dessous)

# Rotation des formats — best practice 2026 actualisée :
# - Carousel (PDF) = format roi : 6.6% engagement, +278% vs text-only (Dataslayer, Buffer 2026)
# - Text long contrarian = alternance utile (700-1200 chars, voix Victor)
# - Polls RETIRÉS du roulement : 1.78x reach MAIS 0.37x engagement (reach trap qui kill l'algo)
#   (van der Blom Algorithm InSights 2025, Dataslayer Feb 2026)
# Switch carrousel → text-only après MAX_SAME_FORMAT_STREAK carrousels consécutifs.
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
# 202605 disponible depuis 2026-05-11 (Microsoft Learn release notes).
# Aucun changement sur /rest/posts entre 202604 et 202605 → bump safe pour la publication.
# Note : 202605 introduit un breaking change sur memberCreatorPostsAnalytics
# (metricType object → string), sans impact ici tant qu'on n'a pas le scope.
LINKEDIN_API_VERSION = "202605"

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
# Nb de sujets récemment publiés injectés au scorer RSS pour l'anti-répétition sémantique.
RECENT_TOPICS_FOR_SCORING = 8
MAX_DETECTOR_RETRIES = 2

# API resilience
ANTHROPIC_MAX_ATTEMPTS = 3
ANTHROPIC_RETRY_BASE_DELAY = 5
REQUESTS_TIMEOUT = 30
SQLITE_TIMEOUT = 10
