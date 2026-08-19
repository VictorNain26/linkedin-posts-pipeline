"""
Configuration centrale du pipeline LinkedIn posts.

Architecture single-mode :
- 100% evergreen orienté prospect fondateur/CTO de startup et PME tech FR
- Pipeline ancré sur l'actualité IA (RSS) avec angle BUSINESS systématique
- Recalibrage 2026-06 : la cible "PDG d'usine non-tech" écrivait pour une audience
  absente du réseau (démographie réelle : Paris tech, devs + fondateurs). La cible
  est désormais le fondateur/CTO tech-aware mais non expert IA — présent dans le
  réseau ET acheteur d'intégration IA.
- Rotation de registres éditoriaux : pain (mise en garde) / pedagogie (comment-faire)
  / preuve (retour terrain ancré sur victor_stories.json — jamais inventé)
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
    # text_body/rewrite : corps text-only cible 1300-2000 chars (~700 tokens + overhead
    # JSON) — marge pour ne pas tronquer (truncation = TruncatedToolUseError, pas de retry)
    "text_body": 1200,
    "rewrite": 1200,
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

Stratège B2B : tu sais ce qui fait stopper le pouce d'un fondateur de startup ou
d'un CTO de PME tech. Un post performant extrait le bénéfice ou le risque CONCRET
pour le lecteur. Un post raté reformule un communiqué de presse en changeant les mots.
</role>

<mission>
Écrire les posts LinkedIn de Victor pour qu'il soit perçu comme un partenaire terrain
qui comprend les vrais enjeux métier — pas comme un agrégateur d'actualités tech.
Victor vend de la confiance autant que de la technique.
</mission>
</persona>"""

AUDIENCE_BLOCK = """<audience>
<who>
Fondateurs, CEOs et CTOs de startups et PME tech françaises (forte densité Paris).
Ils construisent un produit ou un service digital, envisagent d'intégrer l'IA dans
leur produit ou leurs process, et décident du budget et des prestataires.
Ils sont à l'aise avec le vocabulaire produit/tech de base (API, prod, POC, SaaS)
mais NE SONT PAS experts IA/ML — c'est précisément pour ça qu'ils suivent Victor.
</who>

<secondary_audience>
Le réseau de Victor compte aussi beaucoup de développeurs. Pas la cible commerciale,
mais ce sont eux qui likent et repartagent en premier — un post qu'un dev a envie
d'envoyer à SON fondateur voyage plus loin. Ne méprise jamais les devs dans le texte.
</secondary_audience>

<pain_points priority="high">
- POC qui ne passe jamais en prod : "on a testé ChatGPT en interne, et maintenant ?"
- Budget IA imprévisible : "ça coûte combien quand mon volume fait x10 ?"
- Peur du lock-in fournisseur : "et si OpenAI change ses tarifs dans 18 mois ?"
- Équipe dev prise par la roadmap produit : "personne en interne pour porter le sujet IA"
- Build vs buy : "je fais coder mon agent ou je prends un SaaS sur étagère ?"
- Fiabilité en prod : "ça hallucine devant un client, qui répond ?"
- Conformité RGPD + AI Act : "les données de mes clients partent où, chez qui ?"
- ROI incertain : "comment je mesure si ça marche vraiment ?"
- Dépendance prestataire : "si mon freelance disparaît, qui maintient ?"
</pain_points>

<sensibility>
Vocabulaire business et produit, zéro jargon ML non traduit. Sensibles au
time-to-value, au coût récurrent, à la dette technique. Lisent LinkedIn entre
deux daily, scrollent vite, détectent le marketing creux en une demi-seconde.
</sensibility>
</audience>"""

VOCABULARY_BLOCK = """<vocabulary_rules>
<rule name="jargon-ml-traduit">
Le vocabulaire produit/tech de base passe sans traduction : API, prod, POC, SaaS,
intégration. En revanche le jargon IA/ML spécialisé (RAG, fine-tuning, embeddings,
MCP, agents, tokens) doit être traduit en bénéfice métier à sa PREMIÈRE mention,
puis mentionné AU MAX 1 fois — ensuite paraphrase ("l'outil", "ça", "l'automatisation").

<bad_example>
"Avec du RAG sur vos embeddings, votre agent répond mieux."
</bad_example>

<good_example>
"L'assistant pioche ses réponses dans VOS documents (la technique s'appelle RAG).
Résultat : il arrête d'inventer."
</good_example>
</rule>

<rule name="zero-anglicisme-non-traduit">
Pas d'anglicisme marketing sans traduction FR la première fois.

<bad_example>
"Le POC est ready pour le scale."
</bad_example>

<good_example>
"Le POC est prêt à passer en production."
</good_example>
</rule>

<rule name="test-fondateur-non-ml">
Test final : un fondateur de startup produit, PAS expert en IA, peut-il lire
chaque slide sans ouvrir Google ? Si non → reformule. (Il connaît "API" et "prod",
il ne connaît pas "quantization" ni "context window".)
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
Aucune anecdote personnelle inventée. SEULE exception : une expérience fournie dans
un bloc <victor_story> (vécue et validée par Victor) peut être racontée à la première
personne — fidèlement, sans embellir les chiffres ni ajouter de détails absents.
Hors de ce bloc, aucune histoire perso.
<bad_example>"Mardi dernier, un client m'a appelé en panique..." (aucun <victor_story> fourni)</bad_example>
<good_example>"Sur les projets que je vois passer ces derniers mois, le pattern qui revient c'est..."</good_example>
</rule>

<rule id="no-fictional-scenario">
Aucun scénario imaginaire non marqué comme hypothèse.
<bad_example>"Imaginez que votre équipe utilise Codex pour 100 fichiers par jour."</bad_example>
<good_example>"Si on prend l'exemple du cas Virgin Atlantic cité dans l'article : ..."</good_example>
</rule>

<rule id="no-tech-extrapolation">
Si tu fais une affirmation technique pas dans l'article, formule en question ouverte.
<bad_example>"Dès que vous branchez X à vos données, vous passez sur l'API entreprise."</bad_example>
<good_example>"Comment votre équipe va y accéder concrètement ? À voir selon votre stack."</good_example>
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
<good_example>"Combien de temps pour automatiser ça ? Souvent moins qu'on ne le croit."</good_example>
</rule>

<allowed>
Sans marquage spécial, tu peux :
- Résumer, commenter, analyser le contenu de l'article source
- Citer verbatim les chiffres présents dans l'article
- Décrire les douleurs GÉNÉRALES de l'audience (vécues par toute la cible)
- Poser des questions au lecteur ("Vous avez déjà eu ce souci ?")
- Donner un cadre de réflexion neutre ("3 questions à vous poser avant de vous lancer")
</allowed>
</factual_grounding>"""

# ──────────────────────────────────────────────────────────────
# Voice rules
# ──────────────────────────────────────────────────────────────
VOICE_RULES = """<voice>
<voice_source>
Règles calibrées 2026-06 sur les posts LinkedIn MANUELS de Victor (les 3 plus
performants du compte : 27,7k / 7,5k / 3,4k impressions) et sur victorlenain.fr.
Toute sortie doit pouvoir se glisser dans ce corpus sans rupture de ton.
</voice_source>

<rule name="vouvoiement" priority="critical">
Victor VOUVOIE son audience, toujours : "vous", "votre stack", "vos données".
Le tutoiement est une faute de voix (aucun de ses posts réels ne tutoie).
L'impératif direct est bienvenu : "Prévoyez un contrat de maintenance dès le départ."

<bad_example>"Tu paies déjà ton outil IA. Tu sais ce qu'il te coûte ?"</bad_example>
<good_example>"Vous payez déjà votre outil IA. Vous savez ce qu'il vous coûte ?"</good_example>
</rule>

<style_rules>
- Déclaratif et sobre. Pas de connecteur de remplissage ("Du coup", "En fait", "Tu vois").
- Rythme signature : une phrase explicative, puis des fragments staccato.
  <good_example>"Pas de cahier des charges. Pas de liste de fonctionnalités. Juste une idée, un budget, et de la bonne volonté."</good_example>
- Verdict d'un mot pour clore un constat, avec parcimonie : "Normal." "Classique."
- Chiffres concrets dès que la source en fournit (euros, jours, pourcentages) :
  "500 € par jour", "entre 3 et 10 fonctionnalités pour un MVP". Pas d'à-peu-près mou.
- Clôture en antithèse courte quand la matière s'y prête :
  <good_example>"Un devis réaliste vaut mieux qu'un prix rêvé."</good_example>
  <good_example>"Le vrai calcul, ce n'est pas mon tarif journalier. C'est le coût de ne pas résoudre votre problème."</good_example>
- Anti-hype structurel : ne jamais survendre. La voix Victor assume "Si ça n'apporte
  rien, je le dis." Une limite ou un contre-cas mentionné honnêtement renforce le post.
- Phrases courtes : 15 mots max sur une slide ; dans un post texte, alterner
  phrase développée et phrase courte.
- 1 seule idée par slide
- Aucun em-dash (—) dans le texte visible. Préfère le point ou les deux-points.
- Aucune triade d'adjectifs ("rapide, fiable et scalable" → cliché)
- Aucune intro type "Concrètement," au début d'un paragraphe
</style_rules>

<french_syntax_rules>
TU ÉCRIS EN FRANÇAIS NATIF, pas en français traduit de l'anglais.

<rule name="adverbe-apres-verbe">
En FR, l'adverbe de manière se place APRÈS le verbe simple, jamais avant.
Les calques syntaxiques anglais (du type "mis-+verbe") sont à bannir.

<bad_example>"Si votre prestataire mal configure votre outil IA…" (calque de "misconfigures")</bad_example>
<good_example>"Si votre prestataire configure mal votre outil IA…"</good_example>

<bad_example>"L'équipe mal utilise l'outil." (calque de "misuses")</bad_example>
<good_example>"L'équipe utilise mal l'outil." ou "L'équipe se sert mal de l'outil."</good_example>

<bad_example>"Le modèle bien performe sur ce cas." (calque de "well-performs")</bad_example>
<good_example>"Le modèle performe bien sur ce cas." ou "Le modèle marche bien dans ce cas."</good_example>
</rule>

<rule name="evite-tournures-de-traduction">
Si une phrase sonne comme une traduction littérale d'anglais, réécris-la.

<bad_example>"Vous avez juste à brancher l'API." (calque de "you just have to plug")</bad_example>
<good_example>"Vous n'avez qu'à brancher l'API."</good_example>

<bad_example>"Ça change la donne pour votre équipe." (calque marketing un peu cliché)</bad_example>
<good_example>"Ça change le quotidien de votre équipe." ou neutralement "Ça impacte votre équipe."</good_example>
</rule>
</french_syntax_rules>

<final_test>
Relis ta sortie à côté de ce passage réel de Victor (post TJM, 3,4k impressions) :
"500 € par jour ? Mais vous faites quoi en une journée exactement ? La question
est légitime. Vu de l'extérieur, mon travail est opaque."
Même registre attendu : direct, vouvoyé, concret, zéro emphase. Si ta sortie sonne
plus "marketing" ou plus "traduit de l'anglais" que ça, recommence.
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
# Hashtags — 2 fixes (positionnement) + 1 topical roté par post = 3 au total.
# Recalibré 2026-06 : poids algorithmique des hashtags réduit, 1-3 pertinents
# recommandés (Voketa 2026 ; van der Blom : impact minimal au-delà). 5 hashtags
# créaient en plus un pattern répétitif détectable.
# ──────────────────────────────────────────────────────────────
HASHTAGS_FIXED = "#IntégrationIA #IA"
HASHTAG_TOPICAL_SETS = [
    "#Startup",
    "#PME",
    "#CTO",
]


def hashtags_for(rotation_index: int) -> str:
    """2 hashtags fixes + 1 topical choisi par rotation déterministe (3 au total)."""
    return f"{HASHTAGS_FIXED} {HASHTAG_TOPICAL_SETS[rotation_index % len(HASHTAG_TOPICAL_SETS)]}"


# Backward compat (tests / scripts qui importent encore HASHTAGS)
HASHTAGS = hashtags_for(0)

# ──────────────────────────────────────────────────────────────
# CTA (tenables, pas de promesse d'article fantôme) — variantes rotées pour
# casser la répétition mot-à-mot d'un post à l'autre. Toutes contiennent "DM"
# (marqueur utilisé par ensure_cta).
# ──────────────────────────────────────────────────────────────
CTA_SLIDE_VARIANTS = [
    "Vous voulez en discuter pour votre entreprise ? Mon DM est ouvert.",
    "Vous vous posez la question pour votre boîte ? Écrivez-moi en DM.",
    "Besoin d'un avis extérieur sur votre cas ? DM ouvert.",
]
CTA_POST_SUFFIXES = [
    "💬 DM ouvert si vous voulez en parler.",
    "",  # 1 post sur 3 sans CTA dans le body — le carrousel + commentaire suffisent
    "👇 Le détail en commentaire.",
]


def cta_slide_for(rotation_index: int) -> str:
    return CTA_SLIDE_VARIANTS[rotation_index % len(CTA_SLIDE_VARIANTS)]


def cta_post_suffix_for(rotation_index: int) -> str:
    return CTA_POST_SUFFIXES[rotation_index % len(CTA_POST_SUFFIXES)]


# Backward compat
CTA_SLIDE_TEXT = CTA_SLIDE_VARIANTS[0]
CTA_POST_SUFFIX = CTA_POST_SUFFIXES[0]

# ──────────────────────────────────────────────────────────────
# Registres éditoriaux — rotation déterministe pour casser le mono-registre
# anxiogène (3 posts sur 3 en "mise en garde" avant 2026-06).
# - pain      : mise en garde contrarian ancrée sur une douleur (registre historique)
# - pedagogie : comment-faire actionnable, décryptage positif
# - preuve    : retour terrain première personne — UNIQUEMENT si une anecdote
#               réelle existe dans victor_stories.json (sinon registre sauté)
# ──────────────────────────────────────────────────────────────
REGISTRE_PAIN = "pain"
REGISTRE_PEDAGOGIE = "pedagogie"
REGISTRE_PREUVE = "preuve"
REGISTRES_ROTATION = [REGISTRE_PEDAGOGIE, REGISTRE_PAIN, REGISTRE_PREUVE]

# Banque d'anecdotes réelles de Victor (validées par lui, jamais générées).
# Format : cf. victor_stories.example.json à la racine du repo.
STORIES_PATH = STATE_DIR / "victor_stories.json"

# ──────────────────────────────────────────────────────────────
# Formats de post LinkedIn
# ──────────────────────────────────────────────────────────────
FORMAT_CAROUSEL = "carousel"
FORMAT_TEXT = "text"
FORMAT_POLL = "poll"  # legacy, conservé mais non utilisé par le format_selector (cf. ci-dessous)

# Rotation des formats — best practice 2026 actualisée :
# - Carousel (PDF) = format roi : 6.6% engagement, +278% vs text-only (Dataslayer, Buffer 2026)
# - Text long contrarian = alternance utile (1300-2000 chars : sweet spot AuthoredUp
#   2026 = 1301-2500, <400 chars pénalisé ~-27% ; voix Victor)
# - Polls RETIRÉS du roulement : 1.78x reach MAIS 0.37x engagement (reach trap qui kill l'algo)
#   (van der Blom Algorithm InSights 2025, Dataslayer Feb 2026)
# Switch carrousel → text-only après MAX_SAME_FORMAT_STREAK carrousels consécutifs.
MAX_SAME_FORMAT_STREAK = 3

# Carousel format (best practice 2026 : portrait 4:5).
SLIDE_COUNT_MIN = 5
SLIDE_COUNT_MAX = 10
SLIDE_COUNT_TARGET = 7

# ──────────────────────────────────────────────────────────────
# A/B hooks — 3 formules générées par post ; la formule RETENUE suit une rotation
# least-recently-used (chaque formule doit être exposée en réel pour que la
# comparaison de performances ait un sens — un judge seul choisissait
# prospect_question 9 fois sur 11).
# ──────────────────────────────────────────────────────────────
HOOK_VARIATIONS_COUNT = 3
HOOK_FORMULAS = ["contrarian", "data", "prospect_question"]

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
