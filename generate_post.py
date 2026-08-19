"""
Pipeline d'agents — génère un post LinkedIn (carrousel OU text long) + hook + 1er commentaire.

100% evergreen orienté prospect fondateur/CTO de startup et PME tech FR.
Pipeline ancré sur l'actualité IA (RSS) avec angle BUSINESS systématique.

Le FORMAT est décidé AVANT la génération (select_format) et branche le chemin :
- carousel : agents 1→2→3→4→5→5b→6→7→8 (chemin historique)
- text     : agents 1→2→Text Writer→5→5b→6→7→8 (corps 700-1200 chars, pas de slides)

Le REGISTRE éditorial tourne entre pain / pedagogie / preuve (select_registre) :
- pain      : mise en garde contrarian ancrée douleur
- pedagogie : comment-faire actionnable
- preuve    : retour terrain 1re personne, ancré sur victor_stories.json (jamais inventé)

Agents :
  1. Pain Excavator        (Sonnet) — 3 douleurs prospect
  2. Angle Scout           (Sonnet) — angle selon registre + hook visuel + choix de story
  3. Slide Architect       (Sonnet) — structure les slides (5-10, kinds standard/list/number)
  T. Text Writer           (Sonnet) — corps du post text-only (si format=text)
  4. Victor's Pen          (Sonnet) — réécrit dans la voix de Victor (carousel)
  5. Anti-AI Detector      (Sonnet) — retry-with-feedback sur patterns interdits
  5b. Factual Check        (Haiku)  — cross-check faits vs article (+ story si preuve)
  6. Hook Generator        (Sonnet) — 3 variations hook + body_lines
  7. Hook Judge            (Haiku)  — winner avec formule cible (rotation honnête)
  8. First Comment         (Haiku)  — pitch CTA (1 post /3) ou complément de valeur

Patterns :
- tool_use + JSON Schema forcé → 0 parsing libre
- Sonnet pour créativité, Haiku pour sélection/structure → -30% tokens
- retry-with-feedback sur Anti-AI Detector
- diversité de sujet/angle gérée EN AMONT par le scorer Haiku (rss_fetch.score_relevance) —
  pas de dédup ni de fallback ici : on génère sur l'article retenu, point
- grounding : corps d'article extrait (trafilatura) sur l'article retenu, digest Haiku si long
- 0 fallback silencieux : NoUsableNewsError si aucune news en entrée
- 0 fabrication : règle FACTUAL_GROUNDING_RULES dans system block 2 + banque de stories réelles
"""

import json
import re
import sys
import time

from agents import (
    ANGLE_TOOL,
    CTA_COMMENT_TOOL,
    FACTUAL_CHECK_TOOL,
    HOOK_JUDGE_TOOL,
    HOOK_VARIANTS_TOOL,
    PAIN_TOOL,
    SLIDES_TOOL,
    TEXT_BODY_TOOL,
    VIOLATIONS_TOOL,
    _load_learnings_block,
    _system_with_learnings,
    get_story,
    load_stories,
    stories_index_block,
    story_block,
)
from anthropic_client import call_tool, get_run_usage_summary, get_run_usage_totals, reset_run_usage
from config import (
    ANTI_AI_PATTERNS,
    FORMAT_CAROUSEL,
    GROUNDING_FULLTEXT_MAX_CHARS,
    HAIKU_MODEL,
    HOOK_FORMULAS,
    MAX_DETECTOR_RETRIES,
    REGISTRE_PAIN,
    REGISTRE_PEDAGOGIE,
    REGISTRE_PREUVE,
    SLIDE_COUNT_MAX,
    SLIDE_COUNT_MIN,
    SLIDE_COUNT_TARGET,
    SONNET_MODEL,
    TOKEN_BUDGETS,
    cta_post_suffix_for,
    cta_slide_for,
    hashtags_for,
)
from format_selector import select_format, select_registre
from history import published_count, recent_winner_formulas, recent_winning_hooks
from rss_fetch import fetch_article_text

# Re-exports pour rétrocompat (tests, scripts externes qui importeraient depuis generate_post)
__all__ = [
    "ANGLE_TOOL",
    "CTA_COMMENT_TOOL",
    "FACTUAL_CHECK_TOOL",
    "HOOK_JUDGE_TOOL",
    "HOOK_VARIANTS_TOOL",
    "PAIN_TOOL",
    "SLIDES_TOOL",
    "VIOLATIONS_TOOL",
    "_load_learnings_block",
    "_system_with_learnings",
]


# ──────────────────────────────────────────────────────────────
# Agents (les 8 fonctions agent1..agent8 ci-dessous)
# Les schemas tool_use et l'injection learnings ont été extraits dans agents/
# ──────────────────────────────────────────────────────────────
def agent1_pain_excavator(article_ctx: str) -> list[str]:
    """Identifie 3 douleurs RÉELLES du prospect, à la lecture de l'article."""
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "Mets-toi dans la tête de l'AUDIENCE (cf. system). Cette personne lit l'article ci-dessus.\n\n"
            "Identifie 3 DOULEURS RÉELLES que cette annonce/article fait remonter chez elle.\n"
            "Ces douleurs doivent être :\n"
            "- VÉCUES par l'audience (pas inventées)\n"
            "- formulées de SON point de vue (pas du tien)\n"
            "- concrètes (un budget flou, un délai, un risque, une frustration)\n\n"
            "Pas d'anecdote fictive. Pas de chiffre inventé. Une phrase par douleur."
        ),
        tool=PAIN_TOOL,
        max_tokens=TOKEN_BUDGETS["pain"],
    )
    return out["pains"]


_REGISTRE_TASKS = {
    REGISTRE_PAIN: (
        '<task registre="pain">\n'
        "Trouve l'angle MISE EN GARDE unique de ce post.\n"
        "L'angle doit faire 3 choses simultanément :\n"
        "1. COMMENTER l'article (l'article reste la source factuelle — pas d'invention)\n"
        "2. PARLER à AU MOINS UNE des douleurs ci-dessus, dans le vocabulaire de la cible\n"
        "3. SURPRENDRE ou contredire une idée reçue largement répandue chez les fondateurs/CTOs\n"
        "</task>\n"
    ),
    REGISTRE_PEDAGOGIE: (
        '<task registre="pedagogie">\n'
        "Trouve l'angle COMMENT-FAIRE unique de ce post. Pas de mise en garde, pas de peur :\n"
        "le lecteur doit repartir avec une démarche ou une grille de lecture utilisable.\n"
        "L'angle doit faire 3 choses simultanément :\n"
        "1. COMMENTER l'article (source factuelle unique — pas d'invention)\n"
        "2. EXTRAIRE la démarche actionnable : comment s'y prendre, par quoi commencer,\n"
        "   comment décider (build vs buy, quel périmètre, quelles étapes)\n"
        "3. DONNER ENVIE d'essayer — le ton est constructif, pas alarmiste\n"
        "</task>\n"
    ),
    REGISTRE_PREUVE: (
        '<task registre="preuve">\n'
        "Trouve l'angle RETOUR TERRAIN unique de ce post : l'article sert de déclencheur,\n"
        "et UNE expérience réelle de Victor (cf. <victor_stories_index>) sert de preuve.\n"
        "L'angle doit faire 3 choses simultanément :\n"
        "1. RELIER l'article à l'expérience choisie (story_id) — lien NATUREL, pas forcé\n"
        "2. RACONTER ce que Victor a vu/fait sur le terrain (les faits de la story, fidèlement)\n"
        "3. EN TIRER une leçon transférable pour le lecteur\n"
        'Si AUCUNE story ne colle naturellement à l\'article : story_id="" — le post basculera\n'
        "en mode comment-faire, c'est préférable à un lien artificiel.\n"
        "</task>\n"
    ),
}


def agent2_angle_scout(
    article_ctx: str,
    pains: list[str],
    recent_hooks: list[str] | None = None,
    registre: str = REGISTRE_PAIN,
    stories: list[dict] | None = None,
) -> dict:
    """Trouve un angle éditorial business selon le registre, ancré sur les douleurs prospect."""
    recent_block = ""
    if recent_hooks:
        recent_block = (
            "<recent_hooks_to_avoid>\n"
            "Accroches des derniers posts publiés. Ton angle doit être DISTINCT : autre douleur, "
            "autre formulation, autre porte d'entrée. Ne recycle ni le même verbe d'attaque ni le même schéma.\n"
            + "\n".join(f"- {h}" for h in recent_hooks)
            + "\n</recent_hooks_to_avoid>\n\n"
        )
    stories_block_txt = ""
    if registre == REGISTRE_PREUVE and stories:
        stories_block_txt = stories_index_block(stories) + "\n\n"
    return call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<pains_identified>\n"
            + "\n".join(f"- {p}" for p in pains)
            + "\n</pains_identified>\n\n"
            + recent_block
            + stories_block_txt
            + _REGISTRE_TASKS[registre]
            + "\n<critical>\n"
            "PIÈGE 1 : l'article peut être très tech (Codex, GPT-5, API). Ton angle\n"
            "NE DOIT PAS rester sur le terrain tech. Il doit PIVOTER vers le terrain business.\n"
            "Un fondateur de startup produit, pas expert IA, doit reconnaître SA douleur.\n"
            "PIÈGE 2 : l'AUDIENCE DU POST RESTE LA CIBLE (fondateurs/CTOs de startups et PME\n"
            "tech FR), PAS l'audience de l'article. Un article écrit pour des investisseurs,\n"
            "des RH ou des devs ne change pas à qui TU parles.\n"
            "<bad_angle>\"Un chiffre inventé dans un mémo d'investissement : la crédibilité\n"
            "auprès des LP part avec.\" → parle aux fonds d'investissement, pas à la cible.</bad_angle>\n"
            "<good_angle>\"L'IA accélère l'analyse de dossiers chez les investisseurs. La même\n"
            'mécanique vaut pour VOS devis : qui vérifie ce que le modèle sort ?" → ramène\n'
            "le sujet de l'article vers la cible.</good_angle>\n"
            "</critical>\n\n"
            "<positioning_anchor>\n"
            "Victor est INTÉGRATEUR IA — son audience le suit pour ça. Si l'article n'est PAS\n"
            "directement sur l'IA (ex : conformité/RGPD/CNIL, cloud, cybersécurité, organisation,\n"
            "management), ton angle DOIT créer un PONT EXPLICITE vers l'intégration IA. Sans ce fil\n"
            "IA, le post brouille le positionnement (et les hashtags #IA deviennent mensongers).\n"
            "Ponts types : « vos agents IA tournent chez un fournisseur tiers », « l'IA que vous\n"
            "déployez traite des données clients », « avant d'automatiser avec l'IA, qui est\n"
            "responsable ? ». Si l'article EST déjà sur l'IA, ignore ce bloc.\n"
            "</positioning_anchor>\n\n"
            "<bad_examples>\n"
            '<bad_angle>"OpenAI lance Codex. Il automatise la production de code."</bad_angle>\n'
            "  → Reste tech, ne parle d'aucune douleur business.\n"
            '<bad_angle>"L\'IA va remplacer 30% des développeurs."</bad_angle>\n'
            "  → Prédiction non sourcée + clivant sans valeur ajoutée.\n"
            "</bad_examples>\n\n"
            "<good_examples>\n"
            '<good_angle registre="pain">"Un label Gartner \'leader\' ne vous dit pas combien ça coûte chez vous."</good_angle>\n'
            "  → Pivote du fait tech (classement) vers la douleur budget.\n"
            '<good_angle registre="pain">"Vous signez pour Codex aujourd\'hui. Vous changez d\'avis dans 18 mois ?"</good_angle>\n'
            "  → Pivote vers la douleur lock-in fournisseur.\n"
            '<good_angle registre="pedagogie">"Avant de brancher l\'IA sur votre support : 3 étapes que cet article confirme."</good_angle>\n'
            "  → Démarche actionnable tirée de l'article, ton constructif.\n"
            '<good_angle registre="pedagogie">"Build ou buy pour votre agent IA ? L\'article donne un critère simple : le volume."</good_angle>\n'
            "  → Grille de décision utilisable tout de suite.\n"
            '<good_angle registre="preuve">"Cet article annonce -40% de tickets avec un agent IA. J\'ai vu le même chiffre chez un client — mais pas là où on l\'attendait."</good_angle>\n'
            "  → Article = déclencheur, expérience réelle = preuve, leçon transférable.\n"
            "</good_examples>\n\n"
            "<hook_visual_constraints>\n"
            "Hook slide 1 : 1 phrase MAX 8 mots, percutante, vue mobile.\n"
            '- Interpelle le décideur directement ("Vous", "Votre", verbe d\'action) — VOUVOIEMENT\n'
            "- N'inclut PAS le nom de Victor\n"
            "- N'inclut PAS de jargon tech non traduit\n"
            "</hook_visual_constraints>"
        ),
        tool=ANGLE_TOOL,
        max_tokens=TOKEN_BUDGETS["angle"],
    )


_REGISTRE_MIDDLE_SLIDES = {
    REGISTRE_PAIN: (
        "- Slides intermédiaires (3 à n-2) : implications business pour le décideur — "
        "ROI, coût, risque, équipe, conformité, lock-in, time-to-value\n"
    ),
    REGISTRE_PEDAGOGIE: (
        "- Slides intermédiaires (3 à n-2) : la DÉMARCHE, étape par étape ou critère par "
        "critère — chaque slide fait avancer le lecteur vers 'je sais par quoi commencer'\n"
    ),
    REGISTRE_PREUVE: (
        "- Slides intermédiaires (3 à n-2) : le récit terrain — situation de départ, ce qui "
        "a été fait, ce qui a surpris, le résultat chiffré (UNIQUEMENT les faits de "
        "<victor_story>), puis la leçon transférable\n"
    ),
}

# Cadres actionnables à faire tourner — l'avant-dernière slide était devenue
# "3 questions à poser avant de..." sur 3 posts consécutifs (moule visible).
_ACTIONABLE_FRAMINGS = (
    "VARIE le cadre actionnable d'un post à l'autre : checklist de critères, erreurs à "
    "éviter, arbre de décision simple (si X → fais Y), ordre de priorité, signal d'alerte "
    "à surveiller. Le moule '3 questions à poser avant de signer' a déjà beaucoup servi — "
    "ne l'utilise que s'il est VRAIMENT le meilleur format pour cette matière."
)


def agent3_slide_architect(
    article_ctx: str, angle: dict, registre: str = REGISTRE_PAIN, cta_slide_text: str = ""
) -> list[dict]:
    """Structure les slides en commentant l'article pour le décideur."""
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            f"<context>\n"
            f"Registre éditorial : {registre}\n"
            f"Angle retenu : {angle['angle']}\n"
            f"Hook slide 1 (à reprendre exactement) : {angle['hook']}\n"
            f"</context>\n\n"
            f"<task>\n"
            f"Structure un carrousel LinkedIn entre {SLIDE_COUNT_MIN} et {SLIDE_COUNT_MAX} slides "
            f"(cible idéale : {SLIDE_COUNT_TARGET}). Chaque slide commente l'article pour le décideur.\n"
            f"</task>\n\n"
            f"<slide_count_guide>\n"
            f"- {SLIDE_COUNT_MIN}-6 slides : sujet simple, 1-2 implications business\n"
            f"- 7-8 slides : sujet riche avec plusieurs implications (ROI + risque + équipe + conformité)\n"
            f"- 9-{SLIDE_COUNT_MAX} slides : uniquement si vraiment nécessaire (rare)\n"
            f"</slide_count_guide>\n\n"
            f"<structure_template>\n"
            f"- Slide 1 : reprend EXACTEMENT le hook visuel fourni\n"
            f"- Slide 2 : résumé factuel de l'article en 1 phrase clé (zéro invention)\n"
            + _REGISTRE_MIDDLE_SLIDES[registre]
            + f"- Avant-dernière slide : recommandation actionnable. {_ACTIONABLE_FRAMINGS}\n"
            f"- DERNIÈRE slide : CTA — DOIT contenir le texte '{cta_slide_text}'\n"
            f"</structure_template>\n\n"
            "<slide_kinds>\n"
            "- kind=list pour toute énumération (checklist, questions, étapes) : main = titre, "
            "items = les entrées. NE JAMAIS écraser une liste dans le champ sub.\n"
            "- kind=number quand UN chiffre de l'article mérite d'être le héros de la slide : "
            "main = le chiffre seul ('2 M', '-40%'), sub = ce qu'il signifie pour le lecteur.\n"
            "- kind=standard pour le reste. Mets en **gras** le ou les 1-2 mots pivots du main.\n"
            "- Vise 1-2 slides list/number par carrousel quand la matière s'y prête (rythme visuel).\n"
            "</slide_kinds>\n\n"
            "<slide_rules>\n"
            "- 1 slide = 1 idée. Pas plus.\n"
            "- main = phrase punchy (15 mots max). sub = développement court (optionnel).\n"
            "- Carrousel COURT et DENSE > long et délayé. Si un point manque de fact, retire la slide.\n"
            "- Chiffres uniquement si présents dans l'article source (ou <victor_story>). Jamais inventés.\n"
            "</slide_rules>\n\n"
            "<bad_examples>\n"
            '<bad_slide>"L\'IA va révolutionner votre business !" (cliché vide, pas de fact ancré)</bad_slide>\n'
            '<bad_slide>"73% des PME perdent 4h/semaine" (chiffre fabriqué non sourcé article)</bad_slide>\n'
            '<bad_slide>"Mardi dernier, j\'ai vu un client..." (anecdote hors <victor_story>)</bad_slide>\n'
            '<bad_slide>main: "3 questions à poser." sub: "1. Qui est responsable ? 2. Où vont les données ? 3. Qui prévient ?" (liste écrasée dans sub → kind=list avec items)</bad_slide>\n'
            "</bad_examples>\n\n"
            "<good_examples>\n"
            '<good_slide>kind: "standard", main: "La CNIL contrôle les **pratiques**, pas les intentions." sub: "Bug fournisseur, erreur de config : peu importe. Votre périmètre, vos données."</good_slide>\n'
            '<good_slide>kind: "list", main: "Avant de signer, vérifie :", items: ["Qui est responsable de traitement ?", "Où vont les données ?", "Qui prévient la CNIL ?"]</good_slide>\n'
            '<good_slide>kind: "number", main: "2 M", sub: "d\'appels traités chaque mois par leur standard IA. Si ça s\'arrête, votre accueil aussi."</good_slide>\n'
            "</good_examples>"
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["architect"],
    )
    return out["slides"]


def _outline_str(slides: list[dict]) -> str:
    """Sérialise les slides (kind/main/sub/items) pour les prompts de réécriture."""
    lines = []
    for i, s in enumerate(slides):
        parts = [f"Slide {i + 1} [{s.get('kind', 'standard')}] — main: {s['main']}"]
        if s.get("sub"):
            parts.append(f"sub: {s['sub']}")
        if s.get("items"):
            parts.append("items: " + " ; ".join(s["items"]))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _slides_text(slides: list[dict]) -> str:
    """Texte brut des slides (pour détecteurs)."""
    return "\n".join(
        f"S{i + 1}: {s['main']} {s.get('sub', '')} {' '.join(s.get('items', []))}".strip()
        for i, s in enumerate(slides)
    )


def agent4_victors_pen(article_ctx: str, slides_outline: list[dict]) -> list[dict]:
    """Réécrit dans la voix de Victor SANS introduire d'invention."""
    outline_str = _outline_str(slides_outline)
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<task>\n"
            "Réécris le carrousel ci-dessous dans la voix exacte de Victor (cf. <voice> system).\n"
            "Tu es un ré-écrivain, pas un créatif : tu changes le phrasé, pas le fond.\n"
            "</task>\n\n"
            "<preserve>\n"
            "- Structure : même nombre de slides, même ordre, mêmes kind et items "
            "(réécris le phrasé des items, pas leur nombre)\n"
            "- Les marqueurs **gras** sur les mots pivots (déplace-les si ta reformulation "
            "change le mot pivot, mais garde 1-2 par slide standard)\n"
            "- Angle, hook slide 1, CTA final : intacts\n"
            "- Faits de l'article source (et de <victor_story> si présent) : seules sources autorisées\n"
            "</preserve>\n\n"
            "<modify>\n"
            "- Phrasé pour matcher la voix directe et sobre de Victor (vouvoiement)\n"
            "- Casser les phrases trop longues en 2 phrases courtes, ou en fragments\n"
            '  staccato ("Pas de cahier des charges. Pas de liste.")\n'
            '- Remplacer le formel par le direct ("il est nécessaire de" → "prévoyez", "vous devez")\n'
            "- Appliquer les règles de syntaxe FR native (<french_syntax_rules>)\n"
            "</modify>\n\n"
            "<forbidden>\n"
            "- AJOUTER chiffres, anecdotes, situations non présents dans l'outline ou l'article\n"
            '- Inventer des détails pour rendre "plus crédible"\n'
            "- Introduire des buzzwords ou patterns AI (cf. system)\n"
            "</forbidden>\n\n"
            "<rewrite_examples>\n"
            "<example>\n"
            '  before: "Il est nécessaire d\'évaluer méticuleusement les risques avant le déploiement."\n'
            '  after: "Évaluez les risques avant de déployer. Non négociable."\n'
            "</example>\n"
            "<example>\n"
            '  before: "Les organisations doivent prendre en considération la conformité RGPD."\n'
            '  after: "La conformité RGPD se gère en amont. Pas après."\n'
            "</example>\n"
            "</rewrite_examples>\n\n"
            "<outline_to_rewrite>\n" + outline_str + "\n</outline_to_rewrite>"
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["pen"],
    )
    return out["slides"]


def _detect_violations(text: str) -> list[str]:
    """Détection par string matching exact sur ANTI_AI_PATTERNS (rapide)."""
    return [p for p in ANTI_AI_PATTERNS if p in text]


def _detect_semantic_violations(text: str) -> list[str]:
    """Détection sémantique des clichés IA + fautes de voix via Haiku — capture les
    variants non couverts par le string matching (ex : 'dans un monde en pleine
    transformation') et le tutoiement du lecteur (la voix du compte vouvoie)."""
    out = call_tool(
        model=HAIKU_MODEL,
        system=[
            {
                "type": "text",
                "text": (
                    "Tu identifies les clichés de texte généré par IA dans un post LinkedIn business. "
                    "Exemples de patterns à détecter : prophéties vagues ('l'IA va révolutionner'), "
                    "superlatifs sans preuve ('incroyable', 'majeur'), révolutions annoncées, "
                    "formules creuses ('dans un monde en constante/pleine évolution/transformation'), "
                    "phrases d'experts pompiers sans ancrage factuel. "
                    "Tu signales AUSSI toute phrase qui TUTOIE le lecteur ('tu', 'ton', 'ta', 'tes' "
                    "adressés au lecteur) : la voix de ce compte vouvoie, le tutoiement est une faute. "
                    "IMPORTANT : ne signale PAS les affirmations business directes ancrées sur des faits, "
                    "ni le mot 'ton' employé comme nom commun ('le ton du message')."
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        user_text=(
            "<texte>\n" + text + "\n</texte>\n\n"
            "<task>Si aucun cliché IA ni tutoiement : clean=true, violations=[]. "
            "Sinon : clean=false, liste chaque phrase suspecte verbatim (max 6).</task>"
        ),
        tool=VIOLATIONS_TOOL,
        max_tokens=300,
    )
    if out.get("clean", True):
        return []
    return out.get("violations", [])


def _find_all_violations(text: str) -> list[str]:
    """Passe 1 string matching exact, passe 2 sémantique Haiku. Dédupliqué, ordre préservé."""
    exact = _detect_violations(text)
    semantic = _detect_semantic_violations(text) if not exact else []
    return list(dict.fromkeys(exact + semantic))


def agent5_anti_ai_detector(slides: list[dict]) -> list[dict]:
    """Retry-with-feedback (CCA-F D4 §4) :
    - Passe 1 : string matching exact (ANTI_AI_PATTERNS)
    - Passe 2 : détection sémantique Haiku (variants non couverts par string match)
    Re-prompt avec violations explicites si l'une ou l'autre détecte quelque chose."""
    current = slides
    for attempt in range(MAX_DETECTOR_RETRIES + 1):
        violations = _find_all_violations(_slides_text(current))
        if not violations:
            return current
        if attempt == MAX_DETECTOR_RETRIES:
            print(f"[agent5] giving up after {attempt} retries, residual: {violations}", file=sys.stderr)
            return current
        violations_str = ", ".join(f"'{v}'" for v in violations)
        out = call_tool(
            model=SONNET_MODEL,
            system=_system_with_learnings(),
            user_text=(
                "Le draft ci-dessous contient encore des patterns interdits.\n"
                f"PATTERNS DÉTECTÉS À ÉLIMINER : {violations_str}\n\n"
                "Réécris en supprimant CES patterns spécifiques. Garde structure et sens.\n\n"
                "Draft actuel :\n" + _outline_str(current)
            ),
            tool=SLIDES_TOOL,
            max_tokens=TOKEN_BUDGETS["rewrite"],
        )
        current = out["slides"]
    return current


def agent5_anti_ai_detector_text(body: str) -> str:
    """Même boucle retry-with-feedback que agent5, sur le corps d'un post text-only."""
    current = body
    for attempt in range(MAX_DETECTOR_RETRIES + 1):
        violations = _find_all_violations(current)
        if not violations:
            return current
        if attempt == MAX_DETECTOR_RETRIES:
            print(
                f"[agent5-text] giving up after {attempt} retries, residual: {violations}",
                file=sys.stderr,
            )
            return current
        violations_str = ", ".join(f"'{v}'" for v in violations)
        out = call_tool(
            model=SONNET_MODEL,
            system=_system_with_learnings(),
            user_text=(
                "Le corps de post ci-dessous contient encore des patterns interdits.\n"
                f"PATTERNS DÉTECTÉS À ÉLIMINER : {violations_str}\n\n"
                "Réécris en supprimant CES patterns spécifiques. Garde le sens, la longueur "
                "(700-1200 chars) et les sauts de ligne.\n\n"
                "Corps actuel :\n" + current
            ),
            tool=TEXT_BODY_TOOL,
            max_tokens=TOKEN_BUDGETS["pen"],
        )
        current = out["body"]
    return current


_FACTCHECK_SYSTEM = [
    {
        "type": "text",
        "text": (
            "Tu vérifies la cohérence factuelle entre des sources fournies et un contenu LinkedIn. "
            "Ton rôle : détecter les chiffres, affirmations ou faits dans le contenu "
            "qui ne peuvent PAS être tracés aux sources (article, et bloc <victor_story> "
            "éventuel — les faits d'une <victor_story> sont des sources valides). "
            "NE PAS signaler les interprétations ou angles éditoriaux légitimes — "
            "seuls les faits inventés sont des violations."
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


def _factual_violations(article_ctx: str, content_text: str) -> list[str]:
    out = call_tool(
        model=HAIKU_MODEL,
        system=_FACTCHECK_SYSTEM,
        user_text=(
            f"{article_ctx}\n\n"
            "<contenu_a_verifier>\n" + content_text + "\n</contenu_a_verifier>\n\n"
            "<task>Compare chaque fait/chiffre du contenu aux sources. "
            "Si tout est sourcé : clean=true, violations=[]. "
            "Sinon : clean=false, liste chaque claim non sourcé (verbatim, max 5).</task>"
        ),
        tool=FACTUAL_CHECK_TOOL,
        max_tokens=400,
    )
    if out.get("clean", True):
        return []
    return out.get("violations", [])


def agent5b_factual_check(article_ctx: str, slides: list[dict]) -> list[dict]:
    """Cross-check faits/chiffres des slides vs sources (Haiku).
    Si violations trouvées : Sonnet réécrit les slides fautives (1 tentative)."""
    violations = _factual_violations(article_ctx, _slides_text(slides))
    if not violations:
        return slides
    print(f"[agent5b] factual violations detected: {violations}", file=sys.stderr)

    violations_str = " | ".join(violations)
    fixed = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "Les slides ci-dessous contiennent des affirmations NON présentes dans les sources.\n"
            f"VIOLATIONS : {violations_str}\n\n"
            "Réécris en remplaçant chaque violation par :\n"
            "- soit le fait réel présent dans les sources,\n"
            "- soit une reformulation en question ouverte si le fait est incertain.\n"
            "Garde la structure, l'angle et le hook intacts.\n\n"
            "Slides à corriger :\n" + _outline_str(slides)
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["rewrite"],
    )
    return fixed["slides"]


def agent5b_factual_check_text(article_ctx: str, body: str) -> str:
    """Cross-check factuel du corps d'un post text-only (même logique que agent5b)."""
    violations = _factual_violations(article_ctx, body)
    if not violations:
        return body
    print(f"[agent5b-text] factual violations detected: {violations}", file=sys.stderr)

    violations_str = " | ".join(violations)
    fixed = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "Le corps de post ci-dessous contient des affirmations NON présentes dans les sources.\n"
            f"VIOLATIONS : {violations_str}\n\n"
            "Réécris en remplaçant chaque violation par le fait réel sourcé, ou une question "
            "ouverte si le fait est incertain. Garde le sens, la longueur (700-1200 chars) "
            "et les sauts de ligne.\n\n"
            "Corps à corriger :\n" + body
        ),
        tool=TEXT_BODY_TOOL,
        max_tokens=TOKEN_BUDGETS["pen"],
    )
    return fixed["body"]


def agent_text_writer(article_ctx: str, angle: dict, registre: str) -> str:
    """Rédige le corps d'un post text-only (1300-2000 chars) dans la voix de Victor.

    Le hook d'ouverture (agent 6), le CTA et les hashtags sont ajoutés autour —
    ce corps doit tenir debout juste après une accroche d'1-2 lignes.
    """
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<context>\n"
            f"Registre éditorial : {registre}\n"
            f"Angle retenu : {angle['angle']}\n"
            "</context>\n\n"
            "<task>\n"
            "Écris le CORPS d'un post LinkedIn text-only (pas de carrousel).\n"
            "1300-2000 caractères (sweet spot engagement 2026 : 1301-2500, AuthoredUp ;\n"
            "sous 400 chars le reach chute). Il commencera juste APRÈS une accroche d'1-2 lignes\n"
            "(écrite séparément) : n'écris PAS d'accroche, entre directement dans le développement.\n"
            "</task>\n\n"
            "<structure>\n"
            "- Paragraphes de 1-2 phrases max, séparés par une ligne vide (style LinkedIn aéré)\n"
            "- Alterne phrase développée et fragments staccato (la signature Victor :\n"
            '  "Pas de cahier des charges. Pas de liste. Juste une idée et un budget.")\n'
            "- Développe L'ANGLE, pas un résumé de l'article : fait marquant → implication\n"
            "  concrète pour le lecteur → comment agir ou décider\n"
            "- Termine sur une phrase qui ouvre : question au lecteur ou antithèse courte\n"
            '  ("Un devis réaliste vaut mieux qu\'un prix rêvé."), pas une morale plate\n'
            "- Pas de hashtags, pas de 'DM ouvert', pas d'emoji de fin (ajoutés après)\n"
            "</structure>\n\n"
            "<rules>\n"
            "- Chiffres uniquement si présents dans les sources fournies. Jamais inventés.\n"
            "- Voix de Victor (cf. <voice>) : VOUVOIEMENT, sobre, concret, syntaxe FR native.\n"
            "- Si <victor_story> est fourni : raconte à la première personne, fidèlement.\n"
            "</rules>"
        ),
        tool=TEXT_BODY_TOOL,
        max_tokens=TOKEN_BUDGETS["text_body"],
    )
    return out["body"]


def agent6_hook_generator(article_ctx: str, angle: dict, content_summary: str) -> list[dict]:
    """Génère 3 variations de hook + body_lines (1 par formule)."""
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<context>\n"
            f"Angle retenu : {angle['angle']}\n"
            f"Hook visuel slide 1 : {angle['hook']}\n"
            f"Aperçu du contenu : {content_summary}\n"
            "</context>\n\n"
            "<task>\n"
            "Écris 3 hooks pour le TEXTE du post LinkedIn (visible AVANT le 'See more'),\n"
            "chacun accompagné de 1-3 body_lines courtes qui le prolongent.\n"
            "Ces hooks doivent être DIFFÉRENTS du hook visuel de la slide 1 (qui est plus court).\n"
            "1 hook par formule (contrarian, data, prospect_question).\n"
            "</task>\n\n"
            "<body_lines_rules>\n"
            "- 1-3 lignes courtes (max 140 chars chacune) qui suivent LE hook dans le body\n"
            "- Elles créent la tension ou posent le contexte — elles ne RÉSUMENT PAS le contenu\n"
            "  (un lecteur qui a tout compris ne swipe pas et ne clique pas 'see more')\n"
            "- La dernière peut teaser ce qui suit ('Le détail slide par slide 👇' ou équivalent\n"
            "  sobre), sans formule répétée d'un post à l'autre\n"
            "</body_lines_rules>\n\n"
            "<constraints_per_hook>\n"
            "- Longueur cible : 100-140 chars (cutoff mobile, 80%+ du trafic 2026)\n"
            "- Hard limit : 210 chars (cutoff desktop)\n"
            "- 1 à 2 phrases courtes max\n"
            "- Parle au LECTEUR en le VOUVOYANT (vous/votre, jamais tu/ton) — la voix réelle\n"
            "  de Victor vouvoie. JAMAIS 'Mardi dernier j'ai...' (anecdote fictive interdite)\n"
            "- Ton direct et sobre, pas titre marketing\n"
            "- Pas de template anglais reconnaissable type 'Here's what nobody tells you' "
            "(360Brew détecte sémantiquement les hooks copy-paste)\n"
            "- SYNTAXE FRANÇAISE NATIVE : adverbe APRÈS le verbe ('configure mal', PAS 'mal configure').\n"
            "  Relis chaque hook à voix haute — s'il sonne traduit d'anglais, réécris.\n"
            "</constraints_per_hook>\n\n"
            "<formulas>\n\n"
            '<formula name="contrarian">\n'
            "Challenge une idée reçue du marché ou contredit ce que l'article suggère.\n"
            "<good_example>\"Tout le monde court chercher 'le meilleur LLM'. Le vrai problème est ailleurs.\"</good_example>\n"
            "<good_example>\"Un label 'leader Gartner' ne paye pas votre facture d'API. Ce qui compte :\"</good_example>\n"
            '<bad_example>"Voici la dure réalité de l\'IA en entreprise." (cliché vide, autoritaire)</bad_example>\n'
            '<bad_example>"L\'IA va TOUT changer." (banale, pas de contrarian réel)</bad_example>\n'
            "</formula>\n\n"
            '<formula name="data">\n'
            "Cite UN chiffre PRÉSENT dans l'article + son implication business.\n"
            "Si l'article n'a aucun chiffre exploitable, n'utilise PAS cette formule. Mieux vaut un\n"
            "doublon contrarian que d'inventer un '73%' ou un 'McKinsey'.\n"
            '<good_example>"Gartner classe OpenAI Leader 2026 en agents coding. Ça ne vous dit rien sur votre prix final."</good_example>\n'
            '<good_example>"30% de gain de productivité chez Virgin Atlantic avec Codex. Reproductible chez vous ?"</good_example>\n'
            "<bad_example>\"73% des PME passent à l'IA en 2026.\" (chiffre fabriqué, source pas dans l'article)</bad_example>\n"
            "</formula>\n\n"
            '<formula name="prospect_question">\n'
            "Pose une question qui résonne avec UNE douleur précise de la cible.\n"
            '<good_example>"Vous payez déjà votre abonnement Copilot. Vous savez ce que vous y gagnez vraiment ?"</good_example>\n'
            '<good_example>"Vous voulez brancher l\'IA dans vos process. Qui pilote ça en interne ?"</good_example>\n'
            '<bad_example>"Vous voulez gagner du temps avec l\'IA ?" (question vide, banale)</bad_example>\n'
            '<bad_example>"L\'IA pour votre PME, ça vous intéresse ?" (yes/no fermé, aucune accroche)</bad_example>\n'
            "</formula>\n\n"
            "</formulas>"
        ),
        tool=HOOK_VARIANTS_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_generator"],
    )
    return out["variants"]


def agent7_hook_judge(topic: str, variants: list[dict], angle: dict, target_formula: str) -> dict:
    """Valide la formule cible de la rotation, avec veto qualité (Haiku).

    La formule cible suit une rotation least-recently-used entre posts : c'est elle qui
    garantit que chaque formule est EXPOSÉE en réel (sinon le judge seul convergeait sur
    prospect_question 9 fois sur 11 — aucune comparaison de perfs possible). Le judge
    garde un droit de veto si la variante cible est invalide ou faible.

    Reçoit `topic` (titre + résumé court) au lieu du full article_ctx — suffisant pour juger
    des hooks déjà générés, économise ~1500 tokens Haiku par run."""
    variants_str = "\n".join(f"[{v['formula']}] ({len(v['hook'])} chars) {v['hook']}" for v in variants)
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(HAIKU_MODEL),
        user_text=(
            f"<topic>{topic}</topic>\n\n"
            f"<post_angle>{angle['angle']}</post_angle>\n\n"
            "<variants>\n" + variants_str + "\n</variants>\n\n"
            "<task>\n"
            f"FORMULE CIBLE de la rotation : {target_formula}.\n"
            "Choisis la variante de la formule cible, SAUF si elle viole un critère\n"
            "ci-dessous (chiffre fabriqué, anecdote fictive, banale au point de ne rien\n"
            "accrocher, clickbait, calque anglais) — dans ce cas choisis la meilleure des\n"
            "autres et explique le veto.\n"
            "Renvoie : winner_formula + 1-2 phrases de justification (max 300 chars).\n"
            "</task>\n\n"
            "<criteria ordered_by_priority>\n"
            "1. SCROLL-STOP : promesse implicite forte, curiosité, douleur ciblée\n"
            "2. ZÉRO INVENTION FACTUELLE : chiffre précis = doit venir de l'article. Si fabriqué → RECALE\n"
            "3. PARLE AU LECTEUR : pas d'anecdote perso fictive (\"Mardi dernier j'ai…\") → RECALE\n"
            "4. TIENT SA PROMESSE : pas de clickbait — le post doit livrer ce que le hook teaste (algo 2026 pénalise)\n"
            "5. MATCH AUDIENCE NON-TECH : si jargon technique pas traduit → score plus bas\n"
            '6. SYNTAXE FR NATIVE : un calque type "mal configure", "bien utilise" → RECALE\n'
            '7. VOUVOIEMENT : un hook qui tutoie le lecteur ("tu", "ton") → RECALE (la voix Victor vouvoie)\n'
            "</criteria>\n\n"
            "<judgement_examples>\n"
            "<example>\n"
            "  variants:\n"
            '    [contrarian] "L\'IA va TOUT changer en 2026." (banal)\n'
            '    [data] "73% des PME utilisent l\'IA." (chiffre fabriqué)\n'
            '    [prospect_question] "Vous payez déjà votre outil IA. Vous savez combien il vous coûte vraiment ?"\n'
            "  winner: prospect_question\n"
            '  reason: "Seul à toucher une douleur PME précise (coût caché). Les 2 autres : 1 banal, 1 chiffre fabriqué."\n'
            "</example>\n"
            "<example>\n"
            "  variants:\n"
            '    [contrarian] "Vous cherchez le meilleur LLM. Le vrai problème est ailleurs."\n'
            '    [data] "Anthropic vient de sortir Claude 5. 60% plus rapide selon eux."\n'
            '    [prospect_question] "Vous hésitez entre Claude et GPT ?"\n'
            "  winner: contrarian\n"
            '  reason: "Contrarian le plus actionnable. Le data est anecdotique (perf, pas business). Le question est trop tech."\n'
            "</example>\n"
            "</judgement_examples>"
        ),
        tool=HOOK_JUDGE_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_judge"],
    )
    return out


def agent8_cta_comment(topic: str, angle: dict, mode: str = "pitch") -> str:
    """1er commentaire sous le post (Haiku). Deux modes rotés :
    - "pitch"  (1 post sur 3) : CTA commercial direct avec livrable nommé
    - "valeur" (2 posts sur 3) : complément utile, zéro vente — un pitch à chaque post
      lessivait la crédibilité (audience de pairs, ~quelques centaines d'impressions)

    Reçoit `topic` (titre + résumé court) au lieu du full article_ctx — l'angle capture
    déjà l'essence du sujet, économise ~1500 tokens Haiku par run.

    IMPORTANT 2026 : aucun lien externe dans le commentaire. LinkedIn pénalise
    jusqu'à -80% la visibilité des commentaires contenant un lien (Voketa Q1 2026,
    ConnectSafely 2026). Le canal d'action = "DM ouvert" uniquement.
    """
    if mode == "valeur":
        return _agent8_comment_valeur(topic, angle)
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(HAIKU_MODEL),
        user_text=(
            f"<topic>{topic}</topic>\n\n"
            f"<post_angle>{angle['angle']}</post_angle>\n\n"
            "<task>\n"
            "Écris le 1er commentaire que Victor poste sous son propre post.\n"
            "C'est un CTA DIRECT vers une action — pas une question d'engagement.\n"
            "Objectif : faire passer le prospect de la lecture à l'action (DM).\n"
            "</task>\n\n"
            '<format length="200-400 chars">\n'
            "1. UNE phrase de transition courte ancrée sur le sujet du post (1 ligne)\n"
            "2. LE CTA explicite : audit gratuit / appel découverte / sparring 30min / autre\n"
            "3. LE LIVRABLE TANGIBLE : ce que le prospect repart AVEC, concrètement\n"
            '4. CANAL : "DM ouvert" (aucun lien externe)\n'
            "</format>\n\n"
            '<rule name="livrable-obligatoire">\n'
            "Un livrable doit être NOMMÉ et CONCRET. Si tu ne peux pas le nommer, reformule le CTA.\n"
            '<good_livrable>"une feuille de route chiffrée"</good_livrable>\n'
            '<good_livrable>"une short-list de 3 cas d\'usage prioritaires"</good_livrable>\n'
            '<good_livrable>"une grille de risques sur votre stack actuelle"</good_livrable>\n'
            '<good_livrable>"un plan d\'action 30/60/90 jours"</good_livrable>\n'
            '<bad_livrable>"on discute"</bad_livrable>\n'
            '<bad_livrable>"on regarde ensemble"</bad_livrable>\n'
            '<bad_livrable>"on échange sur votre cas"</bad_livrable>\n'
            "</rule>\n\n"
            '<rule name="no-link" priority="critical">\n'
            "AUCUN lien dans le commentaire. AUCUNE URL. AUCUNE mention de site web (victorlenain.fr inclus).\n"
            "LinkedIn pénalise -80% la visibilité des commentaires contenant un lien externe en 2026.\n"
            'Seul canal autorisé : "DM ouvert".\n'
            "</rule>\n\n"
            "<bad_examples>\n"
            '<bad>"N\'hésitez pas à me contacter pour en discuter !" (vague, pas de livrable)</bad>\n'
            '<bad>"Plus d\'infos sur victorlenain.fr 👉" (lien externe → -80% visibilité)</bad>\n'
            '<bad>"DM moi pour qu\'on en parle" (verbe vague, pas de livrable nommé)</bad>\n'
            "</bad_examples>\n\n"
            "<good_examples>\n"
            '<good>"L\'IA pour PME ça commence par savoir quoi automatiser. Si vous voulez clarifier ça pour votre entreprise : 30min en DM. Vous repartez avec une short-list de 3 cas prioritaires et un coût ordre de grandeur. DM ouvert."</good>\n'
            "<good>\"Le 'leader Gartner' ne vous dit pas combien vous allez payer. Mon audit 30min gratuit vous donne une grille de coût réel sur votre stack + un plan de migration sans lock-in. DM ouvert.\"</good>\n"
            "</good_examples>\n\n"
            "<voice>\n"
            'Direct, sobre, voix Victor (VOUVOIEMENT). Pas vendeur agressif. Pas de "Hello !". Pas de 🚀.\n'
            "Relis pour les accords de genre (UN audit, UNE grille, UN plan d'action).\n"
            "</voice>"
        ),
        tool=CTA_COMMENT_TOOL,
        max_tokens=TOKEN_BUDGETS["comment_writer"],
    )
    return out["comment"]


def _agent8_comment_valeur(topic: str, angle: dict) -> str:
    """Mode "valeur" du 1er commentaire : un complément utile, zéro vente."""
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(HAIKU_MODEL),
        user_text=(
            f"<topic>{topic}</topic>\n\n"
            f"<post_angle>{angle['angle']}</post_angle>\n\n"
            "<task>\n"
            "Écris le 1er commentaire que Victor poste sous son propre post.\n"
            "Mode COMPLÉMENT DE VALEUR : tu apportes UN élément utile en plus du post —\n"
            "pas de pitch, pas d'offre, pas de 'audit gratuit'.\n"
            "</task>\n\n"
            '<format length="150-350 chars">\n'
            "UN seul des formats suivants, au choix selon la matière :\n"
            "- La nuance que le post n'avait pas la place de développer\n"
            "- Le premier pas concret si on veut creuser le sujet soi-même\n"
            "- La question que Victor se poserait à la place du lecteur\n"
            "- Le contre-cas : quand le conseil du post ne s'applique PAS\n"
            "</format>\n\n"
            '<rule name="no-link" priority="critical">\n'
            "AUCUN lien, AUCUNE URL, AUCUNE mention de site web.\n"
            "</rule>\n\n"
            '<rule name="no-pitch">\n'
            "Pas de CTA commercial, pas de livrable, pas de 'DM ouvert' obligatoire.\n"
            "Si une invitation à échanger émerge naturellement, une demi-phrase sobre suffit.\n"
            "</rule>\n\n"
            "<bad_examples>\n"
            '<bad>"Si vous voulez un audit gratuit, DM ouvert !" (pitch — pas en mode valeur)</bad>\n'
            '<bad>"Merci d\'avoir lu ! 🙏" (vide)</bad>\n'
            "</bad_examples>\n\n"
            "<good_examples>\n"
            "<good>\"Le point que je n'ai pas développé : la moitié du coût d'un agent vocal, c'est l'intégration à votre agenda et votre CRM. L'abonnement, lui, est public. Demandez toujours le devis TOUT compris.\"</good>\n"
            '<good>"Par où commencer si vous voulez vérifier ça chez vous : exportez 20 tickets support de la semaine dernière et regardez combien suivent le même schéma. C\'est ce volume répétitif qui dit si un agent vaut le coup."</good>\n'
            "</good_examples>\n\n"
            "<voice>\n"
            "Direct, sobre, voix Victor (VOUVOIEMENT). Relis pour les accords de genre (UN audit, UNE grille).\n"
            "</voice>"
        ),
        tool=CTA_COMMENT_TOOL,
        max_tokens=TOKEN_BUDGETS["comment_writer"],
    )
    return out["comment"]


# ──────────────────────────────────────────────────────────────
# Helpers (keywords, slug, formatting)
# ──────────────────────────────────────────────────────────────
_STOPWORDS = {
    "pour",
    "dans",
    "avec",
    "votre",
    "vous",
    "comment",
    "mais",
    "plus",
    "tout",
    "cette",
    "sont",
    "nous",
    "elle",
    "leur",
    "leurs",
    "alors",
    "donc",
    "même",
    "déjà",
    "aussi",
    "très",
    "bien",
    "tous",
    "ainsi",
    "encore",
    "entre",
    "sans",
    "peut",
    "fait",
    "faire",
    "faut",
    "comme",
}


def extract_keywords(topic: str, slides: list[dict]) -> list[str]:
    text = topic + " " + " ".join(s["main"] + " " + s.get("sub", "") for s in slides)
    words = re.findall(r"\b[a-zA-ZÀ-ÿ]{4,}\b", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= 20:
            break
    return out


def slugify(text: str) -> str:
    text = text.lower()
    repl = str.maketrans(
        {
            "à": "a",
            "â": "a",
            "ä": "a",
            "é": "e",
            "è": "e",
            "ê": "e",
            "ë": "e",
            "ï": "i",
            "î": "i",
            "ô": "o",
            "ö": "o",
            "ù": "u",
            "û": "u",
            "ü": "u",
            "ç": "c",
            "ÿ": "y",
            "œ": "oe",
            "æ": "ae",
        }
    )
    text = text.translate(repl)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text[:50].strip("-")


def flatten_slides_to_strings(slides: list[dict]) -> list[str]:
    """Version texte lisible des slides (carousel.md, dashboard). Le PDF generator
    consomme désormais slides_structured (slides.json), pas ces strings."""
    out = []
    for s in slides:
        txt = s["main"]
        if s.get("sub"):
            txt += "\n" + s["sub"]
        for item in s.get("items", []):
            txt += "\n- " + item
        out.append(txt)
    return out


CTA_MARKER = "dm"


def ensure_cta(slides: list[dict], cta_text: str) -> list[dict]:
    """Garantit que la dernière slide contient le CTA (variante rotée du run)."""
    if not slides:
        return slides
    last = slides[-1]
    combined = (last.get("main", "") + " " + last.get("sub", "")).lower()
    if CTA_MARKER not in combined and "discuter" not in combined:
        existing_sub = last.get("sub", "").strip()
        last["sub"] = (existing_sub + " " + cta_text).strip() if existing_sub else cta_text
    return slides


# ──────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────
class NoUsableNewsError(RuntimeError):
    """Aucune news RSS exploitable (vide, déjà couvertes, ou non pertinentes)."""


def _news_to_topic(news: dict) -> str:
    title = (news.get("title") or "").strip()
    summary = (news.get("summary") or "").strip()[:200]
    if not title and not summary:
        raise ValueError(f"news missing title and summary: {news!r}")
    return f"{title}. {summary}".strip(". ")


def _article_context(news: dict) -> str:
    """Construit le bloc 'ARTICLE SOURCE' grounding fourni à tous les agents."""
    title = (news.get("title") or "").strip()
    url = (news.get("url") or "").strip()
    source = (news.get("source") or "").strip()
    summary = (news.get("summary") or "").strip()
    content = (news.get("content") or "").strip()
    body = content if content else summary
    body_text = (
        body if body else "(pas de contenu détaillé — base-toi uniquement sur le titre, n'invente rien)"
    )
    return (
        "═══ ARTICLE SOURCE (seule base factuelle autorisée) ═══\n"
        f"Source : {source}\n"
        f"Titre  : {title}\n"
        f"URL    : {url}\n"
        f"Contenu :\n{body_text}\n"
        "═══════════════════════════════════════════════════"
    )


def _normalize_news_input(topic_input) -> list[dict]:
    if topic_input is None:
        return []
    if isinstance(topic_input, list):
        return [n for n in topic_input if isinstance(n, dict)]
    if isinstance(topic_input, dict):
        return [topic_input]
    if isinstance(topic_input, str) and topic_input.strip():
        return [{"title": topic_input.strip(), "summary": "", "url": ""}]
    return []


DIGEST_TOOL = {
    "name": "submit_article_digest",
    "description": "Soumet un brief factuel de l'article : faits verbatim + thèse, sans interprétation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thesis": {
                "type": "string",
                "description": "Thèse centrale de l'article en 1-2 phrases factuelles (zéro interprétation).",
            },
            "facts": {
                "type": "array",
                "description": (
                    "Faits saillants VERBATIM de l'article : chiffres, pourcentages, dates, "
                    "entités nommées, citations clés. Recopie les nombres exactement."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["thesis", "facts"],
    },
}


def _article_digest(title: str, clean_text: str) -> str:
    """Brief factuel Haiku d'un article long : extraction stricte (chiffres/entités verbatim).

    Réduit les tokens facturés aux agents Sonnet sans perdre les faits. En cas d'échec API,
    le caller bascule sur le texte tronqué (pas de fabrication, pas de plantage).
    """
    out = call_tool(
        model=HAIKU_MODEL,
        system=[
            {
                "type": "text",
                "text": (
                    "Tu extrais un brief FACTUEL d'un article pour qu'un rédacteur s'appuie dessus. "
                    "Règles strictes : recopie chiffres, pourcentages, dates, noms d'entreprises et "
                    "citations EXACTEMENT comme dans l'article. N'interprète pas, n'extrapole pas, "
                    "n'ajoute aucun élément absent du texte."
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        user_text=(
            f"<titre>{title}</titre>\n\n<article>\n{clean_text}\n</article>\n\n"
            "<task>Extrais la thèse centrale + tous les faits saillants verbatim (chiffres, %, "
            "entités, dates, citations). Priorité aux éléments activables pour un post business.</task>"
        ),
        tool=DIGEST_TOOL,
        max_tokens=TOKEN_BUDGETS["article_digest"],
    )
    facts = out.get("facts", [])
    thesis = (out.get("thesis") or "").strip()
    lines: list[str] = []
    if thesis:
        lines.append(f"Thèse : {thesis}")
    if facts:
        lines.append("Faits clés (verbatim de l'article) :")
        lines.extend(f"- {f}" for f in facts)
    return "\n".join(lines)


def _build_grounding_context(news: dict, clean_text: str) -> str:
    """Contexte de grounding fourni aux agents créatifs (Sonnet).

    - Article court (≤ seuil) → texte intégral propre (fidélité max, tokens raisonnables).
    - Article long (> seuil)  → digest factuel Haiku (faits verbatim, ~3-4x moins de tokens Sonnet).
    - Pas de corps (fetch échoué) → fallback résumé RSS via _article_context.
    """
    grounding_news = dict(news)
    if clean_text and len(clean_text) > GROUNDING_FULLTEXT_MAX_CHARS:
        try:
            digest = _article_digest((news.get("title") or "").strip(), clean_text)
        except (RuntimeError, KeyError, ValueError, TypeError) as e:
            print(f"[digest] échec, fallback texte tronqué: {e}", file=sys.stderr)
            digest = ""
        grounding_news["content"] = digest or clean_text[:GROUNDING_FULLTEXT_MAX_CHARS]
    else:
        grounding_news["content"] = clean_text
    return _article_context(grounding_news)


def _select_target_formula() -> str:
    """Formule de hook cible : least-recently-used parmi HOOK_FORMULAS (rotation honnête)."""
    recent = recent_winner_formulas(limit=6)

    def last_use(formula: str) -> int:
        try:
            return recent.index(formula)
        except ValueError:
            return len(recent) + 1

    return max(HOOK_FORMULAS, key=last_use)


def _strip_markdown(text: str) -> str:
    """Retire le markdown que LinkedIn ne rend pas (`**gras**` apparaît littéralement).
    Le marquage **mot** est réservé aux SLIDES (consommé par html_to_pdf.js) — tout
    texte publié tel quel (body, hooks, commentaire) doit en être purgé."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _normalize_punctuation(text: str) -> str:
    """Remplace l'em-dash par deux-points : la règle <voice> l'interdit dans le texte
    visible, mais le bloc hook+body_lines (agents 6/7) ne repasse pas par le détecteur
    agent 5 — normalisation déterministe à l'assemblage, zéro token."""
    return re.sub(r"\s*—\s*", " : ", text)


def _assemble_post_text(hook: str, body_parts: list[str], rotation_index: int) -> str:
    """Body du post : hook + lignes de contexte + CTA roté + hashtags rotés."""
    parts = [_normalize_punctuation(_strip_markdown(hook.strip()))]
    parts.extend(_normalize_punctuation(_strip_markdown(p.strip())) for p in body_parts if p and p.strip())
    suffix = cta_post_suffix_for(rotation_index)
    if suffix:
        parts.append(suffix)
    parts.append(hashtags_for(rotation_index))
    return "\n\n".join(parts)


def generate(topic_input=None) -> dict:  # noqa: PLR0915
    """Génère un post à partir de l'article retenu par le scorer (le 1er de l'entrée).

    Pas de fallback : pertinence ET diversité (pas de doublon de sujet/angle) sont garanties
    en amont par le scorer Haiku (rss_fetch.score_relevance). Si la chaîne d'agents échoue,
    l'exception remonte et le pipeline s'arrête bruyamment — on ne se rabat pas sur un
    article de secours.
    """
    reset_run_usage()
    news_list = _normalize_news_input(topic_input)
    if not news_list:
        raise NoUsableNewsError(
            "Aucune news RSS reçue en entrée. "
            "Vérifie les sources RSS dans config.RSS_SOURCES ou réessaie plus tard."
        )

    winner_news = news_list[0]
    topic = _news_to_topic(winner_news)
    recent_hooks = recent_winning_hooks()

    # ── Décisions éditoriales AVANT génération (déterministes, loggées) ──
    format_choice, format_reason = select_format()
    print(f"[format] {format_choice} — {format_reason}", file=sys.stderr)

    stories = load_stories()
    registre, registre_reason = select_registre(bool(stories))
    print(f"[registre] {registre} — {registre_reason}", file=sys.stderr)

    # Index de rotation déterministe (CTA, hashtags, mode commentaire) : avance à chaque
    # publication réelle, stable entre dry-runs.
    rotation_index = published_count()

    print(f"[generate] article retenu : {topic[:80]}", file=sys.stderr)
    clean_text = fetch_article_text(winner_news.get("url", ""))
    if clean_text:
        print(f"[generate] article body extracted ({len(clean_text)} chars)", file=sys.stderr)
    grounding_ctx = _build_grounding_context(winner_news, clean_text)
    factcheck_ctx = _article_context({**winner_news, "content": clean_text})

    # ── Agents 1-2 : douleurs + angle (communs aux deux formats) ──
    print("[agent1] Pain excavator…", file=sys.stderr)
    pains = agent1_pain_excavator(grounding_ctx)
    print(f"[agent2] Angle scout (registre={registre})…", file=sys.stderr)
    angle = agent2_angle_scout(grounding_ctx, pains, recent_hooks, registre, stories)

    # Registre preuve : injecte la story choisie dans le grounding créatif ET le factcheck
    # (ses faits deviennent du matériau autorisé). Si aucune story ne colle → pedagogie.
    story_id = (angle.get("story_id") or "").strip()
    if registre == REGISTRE_PREUVE:
        story = get_story(stories, story_id) if story_id else None
        if story is None:
            print(
                "[registre] aucune story pertinente pour cet article → bascule en pedagogie",
                file=sys.stderr,
            )
            registre = REGISTRE_PEDAGOGIE
            story_id = ""
        else:
            blk = story_block(story)
            grounding_ctx = grounding_ctx + "\n\n" + blk
            factcheck_ctx = factcheck_ctx + "\n\n" + blk

    # ── Branche par format ──
    if format_choice == FORMAT_CAROUSEL:
        print("[agent3] Slide architect…", file=sys.stderr)
        outline = agent3_slide_architect(grounding_ctx, angle, registre, cta_slide_for(rotation_index))
        print("[agent4] Victor's pen…", file=sys.stderr)
        draft = agent4_victors_pen(grounding_ctx, outline)
        print("[agent5] Anti-AI detector (string + semantic)…", file=sys.stderr)
        cleaned = agent5_anti_ai_detector(draft)
        print("[agent5b] Factual check (slides vs sources)…", file=sys.stderr)
        slides = agent5b_factual_check(factcheck_ctx, cleaned)
        slides = ensure_cta(slides, cta_slide_for(rotation_index))
        text_body = ""
        content_summary = " | ".join(s["main"] for s in slides[:4])
    else:
        print(f"[agent-T] Text writer (registre={registre})…", file=sys.stderr)
        text_body = agent_text_writer(grounding_ctx, angle, registre)
        print("[agent5-text] Anti-AI detector…", file=sys.stderr)
        text_body = agent5_anti_ai_detector_text(text_body)
        print("[agent5b-text] Factual check…", file=sys.stderr)
        text_body = agent5b_factual_check_text(factcheck_ctx, text_body)
        slides = []
        content_summary = text_body[:300]

    keywords = extract_keywords(topic, slides) if slides else extract_keywords(topic, [])

    print("[agent6] Hook generator (3 variants + body lines)…", file=sys.stderr)
    variants = agent6_hook_generator(grounding_ctx, angle, content_summary)

    target_formula = _select_target_formula()
    print(f"[agent7] Hook judge (formule cible : {target_formula})…", file=sys.stderr)
    judge = agent7_hook_judge(topic, variants, angle, target_formula)
    winner_formula = judge["winner_formula"]
    winner_variant = next((v for v in variants if v["formula"] == winner_formula), variants[0])

    comment_mode = "pitch" if rotation_index % 3 == 0 else "valeur"
    print(f"[agent8] First comment (mode={comment_mode})…", file=sys.stderr)
    first_comment = _normalize_punctuation(_strip_markdown(agent8_cta_comment(topic, angle, comment_mode)))

    usage = get_run_usage_totals()
    print(f"[cost] {get_run_usage_summary()}", file=sys.stderr)

    slug = (
        slugify(topic[:40])
        or (slugify(slides[0]["main"][:30]) if slides else "")
        or f"post-{int(time.time())}"
    )
    slides_str = flatten_slides_to_strings(slides)
    body_parts = winner_variant.get("body_lines", []) if format_choice == FORMAT_CAROUSEL else [text_body]
    post_text = _assemble_post_text(winner_variant["hook"], body_parts, rotation_index)

    return {
        "format": format_choice,
        "format_reason": format_reason,
        "registre": registre,
        "registre_reason": registre_reason,
        "story_id": story_id,
        "comment_mode": comment_mode,
        "topic": topic[:120],
        "slug": slug,
        "angle": angle.get("angle", ""),
        "visual_hook": angle.get("hook", ""),
        "hook_variants": variants,
        "hook_target_formula": target_formula,
        "hook_winner_formula": winner_formula,
        "hook_winner_reason": judge["reason"],
        "feed_hook": winner_variant["hook"],
        "slides": slides_str,
        "slides_structured": slides,
        "text_body": text_body,
        "post_text": post_text,
        "first_comment": first_comment,
        "keywords": keywords,
        "article_title": (winner_news.get("title") or "").strip(),
        "article_url": (winner_news.get("url") or "").strip(),
        "cost_usd": usage["cost_usd"],
        "tokens_in": usage["tokens_in"],
        "tokens_out": usage["tokens_out"],
        "tokens_cache_write": usage["tokens_cache_write"],
        "tokens_cache_read": usage["tokens_cache_read"],
    }


# ──────────────────────────────────────────────────────────────
# CLI : lit OBLIGATOIREMENT une liste de news JSON depuis stdin.
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if sys.stdin.isatty():
        print(
            "[generate] ERROR: no stdin. Usage: python rss_fetch.py | python generate_post.py",
            file=sys.stderr,
        )
        sys.exit(2)

    raw = sys.stdin.read().strip()
    if not raw:
        print("[generate] ERROR: empty stdin (no news from RSS)", file=sys.stderr)
        sys.exit(2)

    try:
        topic_input = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[generate] ERROR: stdin is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        result = generate(topic_input)
    except NoUsableNewsError as e:
        print(f"[generate] ERROR: {e}", file=sys.stderr)
        sys.exit(3)

    print(json.dumps(result, ensure_ascii=False, indent=2))
