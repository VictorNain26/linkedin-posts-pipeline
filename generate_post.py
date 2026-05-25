"""
Pipeline 8 agents — génère slides carousel + hook + 1er commentaire LinkedIn.

100% evergreen orienté prospect PME / CTO décisionnaire.
Pipeline ancré sur l'actualité IA (RSS) avec angle BUSINESS systématique.

Agents :
  1. Pain Excavator        (Sonnet) — 3 douleurs prospect
  2. Angle Scout           (Sonnet) — angle contre-intuitif + hook visuel slide 1
  3. Slide Architect       (Sonnet) — structure les slides (5-10, variable)
  4. Victor's Pen          (Sonnet) — réécrit dans la voix de Victor
  5. Anti-AI Detector      (Sonnet) — retry-with-feedback sur patterns interdits
  6. Hook Generator        (Sonnet) — 3 variations de hook texte
  7. Hook Judge            (Haiku)  — sélectionne le winner
  8. CTA Comment           (Haiku)  — 1er commentaire CTA (action + bénéfice + lien)

Patterns :
- tool_use + JSON Schema forcé → 0 parsing libre
- Sonnet pour créativité, Haiku pour sélection/structure → -30% tokens
- retry-with-feedback sur Anti-AI Detector
- dédup keyword overlap par itération sur les news RSS
- 0 fallback silencieux : NoUsableNewsError si aucune news exploitable
- 0 fabrication : règle FACTUAL_GROUNDING_RULES dans system block 2
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
    VIOLATIONS_TOOL,
    _load_learnings_block,
    _system_with_learnings,
)
from anthropic_client import call_tool, get_run_usage_summary, get_run_usage_totals, reset_run_usage
from config import (
    ANTI_AI_PATTERNS,
    CTA_POST_SUFFIX,
    CTA_SLIDE_TEXT,
    HAIKU_MODEL,
    HASHTAGS,
    KEYWORD_OVERLAP_THRESHOLD,
    MAX_DETECTOR_RETRIES,
    PROFILE_URL,
    SLIDE_COUNT_MAX,
    SLIDE_COUNT_MIN,
    SLIDE_COUNT_TARGET,
    SONNET_MODEL,
    TOKEN_BUDGETS,
)
from format_selector import select_format
from history import keyword_overlap_ratio


# Re-exports pour rétrocompat (tests, scripts externes qui importeraient depuis generate_post)
__all__ = [
    "ANGLE_TOOL", "CTA_COMMENT_TOOL", "FACTUAL_CHECK_TOOL", "HOOK_JUDGE_TOOL",
    "HOOK_VARIANTS_TOOL", "PAIN_TOOL", "SLIDES_TOOL", "VIOLATIONS_TOOL",
    "_load_learnings_block", "_system_with_learnings",
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


def agent2_angle_scout(article_ctx: str, pains: list[str]) -> dict:
    """Trouve un angle éditorial business, ancré sur les douleurs prospect."""
    return call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<pains_identified>\n"
            + "\n".join(f"- {p}" for p in pains)
            + "\n</pains_identified>\n\n"
            "<task>\n"
            "Trouve l'angle éditorial UNIQUE de ce post pour la cible PME/CTO non-tech.\n"
            "L'angle doit faire 3 choses simultanément :\n"
            "1. COMMENTER l'article (l'article reste la source factuelle — pas d'invention)\n"
            "2. PARLER à AU MOINS UNE des douleurs ci-dessus, dans le vocabulaire de la cible\n"
            "3. SURPRENDRE ou contredire une idée reçue largement répandue chez les décideurs\n"
            "</task>\n\n"
            "<critical>\n"
            "PIÈGE PRINCIPAL : l'article peut être très tech (Codex, GPT-5, API). Ton angle\n"
            "NE DOIT PAS rester sur le terrain tech. Il doit PIVOTER vers le terrain business.\n"
            "Le PDG d'usine 50 personnes doit reconnaître SA douleur dans ton angle.\n"
            "</critical>\n\n"
            "<bad_examples>\n"
            "<bad_angle>\"OpenAI lance Codex. Il automatise la production de code.\"</bad_angle>\n"
            "  → Reste tech, ne parle d'aucune douleur business.\n"
            "<bad_angle>\"Les meilleurs devs adoptent Codex. Pas les autres.\"</bad_angle>\n"
            "  → Touche les devs, pas le décideur PME non-tech.\n"
            "<bad_angle>\"L'IA va remplacer 30% des développeurs.\"</bad_angle>\n"
            "  → Prédiction non sourcée + clivant sans valeur ajoutée.\n"
            "</bad_examples>\n\n"
            "<good_examples>\n"
            "<good_angle>\"Un label Gartner 'leader' ne te dit pas combien ça coûte chez toi.\"</good_angle>\n"
            "  → Pivote du fait tech (classement) vers la douleur budget.\n"
            "<good_angle>\"L'outil change. Pas le vrai problème : qui l'installe et le maintient ?\"</good_angle>\n"
            "  → Pivote vers la douleur dépendance prestataire + mise en prod fragile.\n"
            "<good_angle>\"Tu signes pour Codex aujourd'hui. Tu changes d'avis dans 18 mois ?\"</good_angle>\n"
            "  → Pivote vers la douleur lock-in fournisseur.\n"
            "</good_examples>\n\n"
            "<hook_visual_constraints>\n"
            "Hook slide 1 : 1 phrase MAX 8 mots, percutante, vue mobile.\n"
            "- Interpelle le décideur directement (\"Tu\", \"Ton\", verbe d'action)\n"
            "- N'inclut PAS le nom de Victor\n"
            "- N'inclut PAS de jargon tech non traduit\n"
            "</hook_visual_constraints>"
        ),
        tool=ANGLE_TOOL,
        max_tokens=TOKEN_BUDGETS["angle"],
    )


def agent3_slide_architect(article_ctx: str, angle: dict) -> list[dict]:
    """Structure les slides en commentant l'article pour le décideur."""
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            f"<context>\n"
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
            f"- Slides intermédiaires (3 à n-1) : implications business pour le décideur — "
            f"ROI, coût, risque, équipe, conformité, lock-in, time-to-value\n"
            f"- Avant-dernière slide : recommandation actionnable (cadre de décision, 3 questions à poser, "
            f"checklist de 3-5 items, etc.)\n"
            f"- DERNIÈRE slide : CTA — DOIT contenir le texte '{CTA_SLIDE_TEXT}'\n"
            f"</structure_template>\n\n"
            "<slide_rules>\n"
            "- 1 slide = 1 idée. Pas plus.\n"
            "- main = phrase punchy (15 mots max). sub = développement court (optionnel).\n"
            "- Carrousel COURT et DENSE > long et délayé. Si un point manque de fact, retire la slide.\n"
            "- Chiffres uniquement si présents dans l'article source. Jamais inventés.\n"
            "</slide_rules>\n\n"
            "<bad_examples>\n"
            "<bad_slide>\"L'IA va révolutionner ton business !\" (cliché vide, pas de fact ancré)</bad_slide>\n"
            "<bad_slide>\"73% des PME perdent 4h/semaine\" (chiffre fabriqué non sourcé article)</bad_slide>\n"
            "<bad_slide>\"Mardi dernier, j'ai vu un client...\" (anecdote inventée)</bad_slide>\n"
            "</bad_examples>\n\n"
            "<good_examples>\n"
            "<good_slide>main: \"La CNIL contrôle les pratiques, pas les intentions.\" sub: \"Bug fournisseur, erreur de config : peu importe. Ton périmètre, tes données.\"</good_slide>\n"
            "<good_slide>main: \"3 questions à poser avant de signer.\" sub: \"Qui est responsable de traitement ? Où vont les données ? Qui prévient la CNIL ?\"</good_slide>\n"
            "</good_examples>"
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["architect"],
    )
    return out["slides"]


def agent4_victors_pen(article_ctx: str, slides_outline: list[dict]) -> list[dict]:
    """Réécrit dans la voix de Victor SANS introduire d'invention."""
    outline_str = "\n".join(
        f"Slide {i + 1} — main: {s['main']}" + (f" | sub: {s.get('sub', '')}" if s.get("sub") else "")
        for i, s in enumerate(slides_outline)
    )
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<task>\n"
            "Réécris le carrousel ci-dessous dans la voix exacte de Victor (cf. <voice> system).\n"
            "Tu es un ré-écrivain, pas un créatif : tu changes le phrasé, pas le fond.\n"
            "</task>\n\n"
            "<preserve>\n"
            "- Structure : même nombre de slides, même ordre\n"
            "- Angle, hook slide 1, CTA final : intacts\n"
            "- Faits de l'article source : seule source factuelle autorisée\n"
            "</preserve>\n\n"
            "<modify>\n"
            "- Phrasé pour matcher la voix orale courte de Victor\n"
            "- Casser les phrases trop longues en 2 phrases courtes\n"
            "- Remplacer le formel par l'oral (\"il faut\" → \"tu dois\", etc.)\n"
            "- Appliquer les règles de syntaxe FR native (<french_syntax_rules>)\n"
            "</modify>\n\n"
            "<forbidden>\n"
            "- AJOUTER chiffres, anecdotes, situations non présents dans l'outline ou l'article\n"
            "- Inventer des détails pour rendre \"plus crédible\"\n"
            "- Introduire des buzzwords ou patterns AI (cf. system)\n"
            "</forbidden>\n\n"
            "<rewrite_examples>\n"
            "<example>\n"
            "  before: \"Il est nécessaire d'évaluer méticuleusement les risques avant le déploiement.\"\n"
            "  after: \"Évalue les risques avant de déployer. C'est pas négociable.\"\n"
            "</example>\n"
            "<example>\n"
            "  before: \"Les organisations doivent prendre en considération la conformité RGPD.\"\n"
            "  after: \"La conformité RGPD, tu la gères en amont. Pas après.\"\n"
            "</example>\n"
            "</rewrite_examples>\n\n"
            "<outline_to_rewrite>\n" + outline_str + "\n</outline_to_rewrite>"
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["pen"],
    )
    return out["slides"]


def _detect_violations(slides: list[dict]) -> list[str]:
    """Détection par string matching exact sur ANTI_AI_PATTERNS (rapide)."""
    text = " ".join(s["main"] + " " + s.get("sub", "") for s in slides)
    return [p for p in ANTI_AI_PATTERNS if p in text]


def _detect_semantic_violations(slides: list[dict]) -> list[str]:
    """Détection sémantique des clichés IA via Haiku — capture les variants non couverts
    par le string matching (ex : 'dans un monde en pleine transformation')."""
    text = "\n".join(
        f"S{i+1}: {s['main']} {s.get('sub', '')}" for i, s in enumerate(slides)
    )
    out = call_tool(
        model=HAIKU_MODEL,
        system=[{
            "type": "text",
            "text": (
                "Tu identifies les clichés de texte généré par IA dans un post LinkedIn business. "
                "Exemples de patterns à détecter : prophéties vagues ('l'IA va révolutionner'), "
                "superlatifs sans preuve ('incroyable', 'majeur'), révolutions annoncées, "
                "formules creuses ('dans un monde en constante/pleine évolution/transformation'), "
                "phrases d'experts pompiers sans ancrage factuel. "
                "IMPORTANT : ne signale PAS les affirmations business directes ancrées sur des faits."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        user_text=(
            "<slides>\n" + text + "\n</slides>\n\n"
            "<task>Si aucun cliché IA : clean=true, violations=[]. "
            "Sinon : clean=false, liste chaque phrase suspecte verbatim (max 6).</task>"
        ),
        tool=VIOLATIONS_TOOL,
        max_tokens=300,
    )
    if out.get("clean", True):
        return []
    return out.get("violations", [])


def agent5_anti_ai_detector(slides: list[dict]) -> list[dict]:
    """Retry-with-feedback (CCA-F D4 §4) :
    - Passe 1 : string matching exact (ANTI_AI_PATTERNS)
    - Passe 2 : détection sémantique Haiku (variants non couverts par string match)
    Re-prompt avec violations explicites si l'une ou l'autre détecte quelque chose."""
    current = slides
    for attempt in range(MAX_DETECTOR_RETRIES + 1):
        exact = _detect_violations(current)
        semantic = _detect_semantic_violations(current) if not exact else []
        violations = list(dict.fromkeys(exact + semantic))  # dédupliqué, ordre préservé
        if not violations:
            return current
        if attempt == MAX_DETECTOR_RETRIES:
            print(f"[agent5] giving up after {attempt} retries, residual: {violations}", file=sys.stderr)
            return current
        violations_str = ", ".join(f"'{v}'" for v in violations)
        outline_str = "\n".join(
            f"Slide {i + 1} — main: {s['main']}" + (f" | sub: {s.get('sub', '')}" if s.get("sub") else "")
            for i, s in enumerate(current)
        )
        out = call_tool(
            model=SONNET_MODEL,
            system=_system_with_learnings(),
            user_text=(
                "Le draft ci-dessous contient encore des patterns interdits.\n"
                f"PATTERNS DÉTECTÉS À ÉLIMINER : {violations_str}\n\n"
                "Réécris en supprimant CES patterns spécifiques. Garde structure et sens.\n\n"
                "Draft actuel :\n" + outline_str
            ),
            tool=SLIDES_TOOL,
            max_tokens=TOKEN_BUDGETS["detector"],
        )
        current = out["slides"]
    return current


def agent5b_factual_check(article_ctx: str, slides: list[dict]) -> list[dict]:
    """Cross-check faits/chiffres des slides vs article source (Haiku).
    Détecte les affirmations inventées ou extrapolées non présentes dans l'article.
    Si violations trouvées : Sonnet réécrit les slides fautives (1 tentative)."""
    slides_text = "\n".join(
        f"S{i+1}: {s['main']} {s.get('sub', '')}" for i, s in enumerate(slides)
    )
    article_ctx_short = article_ctx[:1500] + ("…" if len(article_ctx) > 1500 else "")
    out = call_tool(
        model=HAIKU_MODEL,
        system=[{
            "type": "text",
            "text": (
                "Tu vérifies la cohérence factuelle entre un article source et des slides LinkedIn. "
                "Ton rôle : détecter les chiffres, affirmations ou faits dans les slides "
                "qui ne peuvent PAS être tracés à l'article. "
                "NE PAS signaler les interprétations ou angles éditoriaux légitimes — "
                "seuls les faits inventés sont des violations."
            ),
            "cache_control": {"type": "ephemeral"},
        }],
        user_text=(
            f"{article_ctx_short}\n\n"
            "<slides_to_verify>\n" + slides_text + "\n</slides_to_verify>\n\n"
            "<task>Compare chaque fait/chiffre des slides à l'article. "
            "Si tout est sourcé dans l'article : clean=true, violations=[]. "
            "Sinon : clean=false, liste chaque claim non sourcé (verbatim, max 5).</task>"
        ),
        tool=FACTUAL_CHECK_TOOL,
        max_tokens=400,
    )

    if out.get("clean", True) or not out.get("violations"):
        return slides

    violations = out["violations"]
    print(f"[agent5b] factual violations detected: {violations}", file=sys.stderr)

    outline_str = "\n".join(
        f"Slide {i + 1} — main: {s['main']}" + (f" | sub: {s.get('sub', '')}" if s.get("sub") else "")
        for i, s in enumerate(slides)
    )
    violations_str = " | ".join(violations)
    fixed = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "Les slides ci-dessous contiennent des affirmations NON présentes dans l'article source.\n"
            f"VIOLATIONS : {violations_str}\n\n"
            "Réécris en remplaçant chaque violation par :\n"
            "- soit le fait réel présent dans l'article,\n"
            "- soit une reformulation en question ouverte si le fait est incertain.\n"
            "Garde la structure, l'angle et le hook intacts.\n\n"
            "Slides à corriger :\n" + outline_str
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["detector"],
    )
    return fixed["slides"]


def agent6_hook_generator(article_ctx: str, angle: dict, slides: list[dict]) -> list[dict]:
    """Génère 3 variations de hook texte (1 par formule)."""
    slides_summary = " | ".join(s["main"] for s in slides[:4])
    out = call_tool(
        model=SONNET_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"{article_ctx}\n\n"
            "<context>\n"
            f"Angle retenu : {angle['angle']}\n"
            f"Hook visuel slide 1 : {angle['hook']}\n"
            f"Aperçu slides 1-4 : {slides_summary}\n"
            "</context>\n\n"
            "<task>\n"
            "Écris 3 hooks pour le TEXTE du post LinkedIn (visible AVANT le 'See more').\n"
            "Ces hooks doivent être DIFFÉRENTS du hook visuel de la slide 1 (qui est plus court).\n"
            "1 hook par formule (contrarian, data, prospect_question).\n"
            "</task>\n\n"
            "<constraints_per_hook>\n"
            "- Longueur cible : 100-140 chars (cutoff mobile, 80%+ du trafic 2026)\n"
            "- Hard limit : 210 chars (cutoff desktop)\n"
            "- 1 à 2 phrases courtes max\n"
            "- Tu parles au LECTEUR (tu/vous), JAMAIS 'Mardi dernier j'ai...' (anecdote fictive interdite)\n"
            "- Ton oral, pas titre marketing\n"
            "- Pas de template anglais reconnaissable type 'Here's what nobody tells you' "
            "(360Brew détecte sémantiquement les hooks copy-paste)\n"
            "- SYNTAXE FRANÇAISE NATIVE : adverbe APRÈS le verbe ('configure mal', PAS 'mal configure').\n"
            "  Relis chaque hook à voix haute — s'il sonne traduit d'anglais, réécris.\n"
            "</constraints_per_hook>\n\n"
            "<formulas>\n\n"
            "<formula name=\"contrarian\">\n"
            "Challenge une idée reçue du marché ou contredit ce que l'article suggère.\n"
            "<good_example>\"Tout le monde court chercher 'le meilleur LLM'. Le vrai problème est ailleurs.\"</good_example>\n"
            "<good_example>\"Un label 'leader Gartner' ne paye pas ta facture d'API. Ce qui change la donne :\"</good_example>\n"
            "<bad_example>\"Voici la dure réalité de l'IA en entreprise.\" (cliché vide, autoritaire)</bad_example>\n"
            "<bad_example>\"L'IA va TOUT changer.\" (banale, pas de contrarian réel)</bad_example>\n"
            "</formula>\n\n"
            "<formula name=\"data\">\n"
            "Cite UN chiffre PRÉSENT dans l'article + son implication business.\n"
            "Si l'article n'a aucun chiffre exploitable, n'utilise PAS cette formule. Mieux vaut un\n"
            "doublon contrarian que d'inventer un '73%' ou un 'McKinsey'.\n"
            "<good_example>\"Gartner classe OpenAI Leader 2026 en agents coding. Ça ne te dit rien sur ton prix final.\"</good_example>\n"
            "<good_example>\"30% de gain de productivité chez Virgin Atlantic avec Codex. Reproductible chez toi ?\"</good_example>\n"
            "<bad_example>\"73% des PME passent à l'IA en 2026.\" (chiffre fabriqué, source pas dans l'article)</bad_example>\n"
            "</formula>\n\n"
            "<formula name=\"prospect_question\">\n"
            "Pose une question qui résonne avec UNE douleur précise de la cible.\n"
            "<good_example>\"Tu paies déjà ton abonnement Copilot. Tu sais ce que tu y gagnes vraiment ?\"</good_example>\n"
            "<good_example>\"Tu veux brancher l'IA dans tes process. Qui pilote ça en interne ?\"</good_example>\n"
            "<bad_example>\"Vous voulez gagner du temps avec l'IA ?\" (question vide, banale)</bad_example>\n"
            "<bad_example>\"L'IA pour ta PME, ça t'intéresse ?\" (yes/no fermé, aucune accroche)</bad_example>\n"
            "</formula>\n\n"
            "</formulas>"
        ),
        tool=HOOK_VARIANTS_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_generator"],
    )
    return out["variants"]


def agent7_hook_judge(topic: str, variants: list[dict], angle: dict) -> dict:
    """Sélectionne le hook winner parmi les 3 variations (Haiku).
    Reçoit `topic` (titre + résumé court) au lieu du full article_ctx — suffisant pour juger
    des hooks déjà générés, économise ~1500 tokens Haiku par run."""
    variants_str = "\n".join(f"[{v['formula']}] ({len(v['hook'])} chars) {v['hook']}" for v in variants)
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"<topic>{topic}</topic>\n\n"
            f"<post_angle>{angle['angle']}</post_angle>\n\n"
            "<variants>\n" + variants_str + "\n</variants>\n\n"
            "<task>\n"
            "Choisis LE meilleur des 3 hooks pour la cible PME/CTO non-tech.\n"
            "Renvoie : winner_formula + 1-2 phrases de justification (max 300 chars).\n"
            "</task>\n\n"
            "<criteria ordered_by_priority>\n"
            "1. SCROLL-STOP : promesse implicite forte, curiosité, douleur ciblée\n"
            "2. ZÉRO INVENTION FACTUELLE : chiffre précis = doit venir de l'article. Si fabriqué → RECALE\n"
            "3. PARLE AU LECTEUR : pas d'anecdote perso fictive (\"Mardi dernier j'ai…\") → RECALE\n"
            "4. TIENT SA PROMESSE : pas de clickbait — le post doit livrer ce que le hook teaste (algo 2026 pénalise)\n"
            "5. MATCH AUDIENCE NON-TECH : si jargon technique pas traduit → score plus bas\n"
            "6. SYNTAXE FR NATIVE : un calque type \"mal configure\", \"bien utilise\" → RECALE\n"
            "</criteria>\n\n"
            "<judgement_examples>\n"
            "<example>\n"
            "  variants:\n"
            "    [contrarian] \"L'IA va TOUT changer en 2026.\" (banal)\n"
            "    [data] \"73% des PME utilisent l'IA.\" (chiffre fabriqué)\n"
            "    [prospect_question] \"Tu paies déjà ton outil IA. Tu sais combien il te coûte vraiment ?\"\n"
            "  winner: prospect_question\n"
            "  reason: \"Seul à toucher une douleur PME précise (coût caché). Les 2 autres : 1 banal, 1 chiffre fabriqué.\"\n"
            "</example>\n"
            "<example>\n"
            "  variants:\n"
            "    [contrarian] \"Tu cherches le meilleur LLM. Le vrai problème est ailleurs.\"\n"
            "    [data] \"Anthropic vient de sortir Claude 5. 60% plus rapide selon eux.\"\n"
            "    [prospect_question] \"Tu hésites entre Claude et GPT ?\"\n"
            "  winner: contrarian\n"
            "  reason: \"Contrarian le plus actionnable. Le data est anecdotique (perf, pas business). Le question est trop tech.\"\n"
            "</example>\n"
            "</judgement_examples>"
        ),
        tool=HOOK_JUDGE_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_judge"],
    )
    return out


def agent8_cta_comment(topic: str, angle: dict) -> str:
    """1er commentaire = CTA direct sous le post (Haiku).

    Reçoit `topic` (titre + résumé court) au lieu du full article_ctx — l'angle capture
    déjà l'essence du sujet, économise ~1500 tokens Haiku par run.

    IMPORTANT 2026 : aucun lien externe dans le commentaire. LinkedIn pénalise
    jusqu'à -80% la visibilité des commentaires contenant un lien (Voketa Q1 2026,
    ConnectSafely 2026). Le canal d'action = "DM ouvert" uniquement.
    """
    out = call_tool(
        model=HAIKU_MODEL,
        system=_system_with_learnings(),
        user_text=(
            f"<topic>{topic}</topic>\n\n"
            f"<post_angle>{angle['angle']}</post_angle>\n\n"
            "<task>\n"
            "Écris le 1er commentaire que Victor poste sous son propre post.\n"
            "C'est un CTA DIRECT vers une action — pas une question d'engagement.\n"
            "Objectif : faire passer le prospect de la lecture à l'action (DM).\n"
            "</task>\n\n"
            "<format length=\"200-400 chars\">\n"
            "1. UNE phrase de transition courte ancrée sur le sujet du post (1 ligne)\n"
            "2. LE CTA explicite : audit gratuit / appel découverte / sparring 30min / autre\n"
            "3. LE LIVRABLE TANGIBLE : ce que le prospect repart AVEC, concrètement\n"
            "4. CANAL : \"DM ouvert\" (aucun lien externe)\n"
            "</format>\n\n"
            "<rule name=\"livrable-obligatoire\">\n"
            "Un livrable doit être NOMMÉ et CONCRET. Si tu ne peux pas le nommer, reformule le CTA.\n"
            "<good_livrable>\"une feuille de route chiffrée\"</good_livrable>\n"
            "<good_livrable>\"une short-list de 3 cas d'usage prioritaires\"</good_livrable>\n"
            "<good_livrable>\"une grille de risques sur ta stack actuelle\"</good_livrable>\n"
            "<good_livrable>\"un plan d'action 30/60/90 jours\"</good_livrable>\n"
            "<bad_livrable>\"on discute\"</bad_livrable>\n"
            "<bad_livrable>\"on regarde ensemble\"</bad_livrable>\n"
            "<bad_livrable>\"on échange sur ton cas\"</bad_livrable>\n"
            "</rule>\n\n"
            "<rule name=\"no-link\" priority=\"critical\">\n"
            "AUCUN lien dans le commentaire. AUCUNE URL. AUCUNE mention de site web (victorlenain.fr inclus).\n"
            "LinkedIn pénalise -80% la visibilité des commentaires contenant un lien externe en 2026.\n"
            "Seul canal autorisé : \"DM ouvert\".\n"
            "</rule>\n\n"
            "<bad_examples>\n"
            "<bad>\"N'hésite pas à me contacter pour en discuter !\" (vague, pas de livrable)</bad>\n"
            "<bad>\"Plus d'infos sur victorlenain.fr 👉\" (lien externe → -80% visibilité)</bad>\n"
            "<bad>\"DM moi pour qu'on en parle\" (verbe vague, pas de livrable nommé)</bad>\n"
            "</bad_examples>\n\n"
            "<good_examples>\n"
            "<good>\"L'IA pour PME ça commence par savoir quoi automatiser. Si tu veux clarifier ça pour ton entreprise : 30min en DM. Tu repars avec une short-list de 3 cas prioritaires et un coût ordre de grandeur. DM ouvert.\"</good>\n"
            "<good>\"Le 'leader Gartner' ne te dit pas combien tu vas payer. Mon audit 30min gratuit te donne une grille de coût réel sur ta stack + un plan de migration sans lock-in. DM ouvert.\"</good>\n"
            "</good_examples>\n\n"
            "<voice>\n"
            "Direct, oral, voix Victor. Pas vendeur agressif. Pas de \"Hello !\". Pas de 🚀.\n"
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
    """Pour le PDF generator (qui parse 'main\\nsub')."""
    return [s["main"] + ("\n" + s["sub"] if s.get("sub") else "") for s in slides]


CTA_MARKER = "dm"


def ensure_cta(slides: list[dict]) -> list[dict]:
    """Garantit que la dernière slide contient le CTA."""
    if not slides:
        return slides
    last = slides[-1]
    combined = (last.get("main", "") + " " + last.get("sub", "")).lower()
    if CTA_MARKER not in combined and "discuter" not in combined:
        existing_sub = last.get("sub", "").strip()
        last["sub"] = (existing_sub + " " + CTA_SLIDE_TEXT).strip() if existing_sub else CTA_SLIDE_TEXT
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


def _run_once(article_ctx: str) -> tuple[list[dict], dict]:
    print("[agent1] Pain excavator…", file=sys.stderr)
    pains = agent1_pain_excavator(article_ctx)
    print("[agent2] Angle scout…", file=sys.stderr)
    angle = agent2_angle_scout(article_ctx, pains)
    print("[agent3] Slide architect…", file=sys.stderr)
    outline = agent3_slide_architect(article_ctx, angle)
    print("[agent4] Victor's pen…", file=sys.stderr)
    draft = agent4_victors_pen(article_ctx, outline)
    print("[agent5] Anti-AI detector (string + semantic)…", file=sys.stderr)
    cleaned = agent5_anti_ai_detector(draft)
    print("[agent5b] Factual check (slides vs article)…", file=sys.stderr)
    final = agent5b_factual_check(article_ctx, cleaned)
    return final, angle


def generate(topic_input=None) -> dict:
    """
    Génère un post à partir d'une OU plusieurs news RSS.
    Aucun fallback silencieux : si aucune news n'est exploitable, raise NoUsableNewsError.
    """
    reset_run_usage()
    news_list = _normalize_news_input(topic_input)
    if not news_list:
        raise NoUsableNewsError(
            "Aucune news RSS reçue en entrée. "
            "Vérifie les sources RSS dans config.RSS_SOURCES ou réessaie plus tard."
        )

    last_error: str | None = None
    slides: list[dict] = []
    angle: dict = {}
    keywords: list[str] = []
    topic: str = ""
    article_ctx_winner = ""
    winner_news: dict = {}

    for idx, news in enumerate(news_list):
        topic = _news_to_topic(news)
        article_ctx = _article_context(news)
        print(f"[generate] trying news {idx + 1}/{len(news_list)}: {topic[:80]}", file=sys.stderr)
        try:
            slides, angle = _run_once(article_ctx)
        except (RuntimeError, KeyError, ValueError, TypeError) as e:
            # Anthropic API en échec ou retour mal formé (schema mismatch) → on essaie la news suivante
            last_error = f"news {idx}: {e}"
            print(f"[generate] run failed on news {idx + 1}: {e}", file=sys.stderr)
            continue

        keywords = extract_keywords(topic, slides)
        overlap = keyword_overlap_ratio(keywords)
        if overlap < KEYWORD_OVERLAP_THRESHOLD:
            print(f"[generate] news {idx + 1} accepted (overlap={overlap:.2f})", file=sys.stderr)
            article_ctx_winner = article_ctx
            winner_news = news
            break
        print(
            f"[dedup] news {idx + 1} overlap={overlap:.2f} ≥ {KEYWORD_OVERLAP_THRESHOLD}, trying next",
            file=sys.stderr,
        )
        last_error = f"news {idx} too similar to recent (overlap={overlap:.2f})"
    else:
        raise NoUsableNewsError(
            f"Aucune des {len(news_list)} news RSS n'est exploitable. Dernier motif : {last_error}"
        )

    slides = ensure_cta(slides)

    print("[agent6] Hook generator (3 variants)…", file=sys.stderr)
    variants = agent6_hook_generator(article_ctx_winner, angle, slides)

    print("[agent7] Hook judge…", file=sys.stderr)
    judge = agent7_hook_judge(topic, variants, angle)
    winner_formula = judge["winner_formula"]
    winner_variant = next((v for v in variants if v["formula"] == winner_formula), variants[0])

    print("[agent8] CTA comment writer…", file=sys.stderr)
    first_comment = agent8_cta_comment(topic, angle)

    usage = get_run_usage_totals()
    print(f"[cost] {get_run_usage_summary()}", file=sys.stderr)

    # Décide du format pour cette publication (carousel / text / poll)
    format_choice, format_reason = select_format()
    print(f"[format] {format_choice} — {format_reason}", file=sys.stderr)

    slug = (
        slugify(topic[:40])
        or (slugify(slides[0]["main"][:30]) if slides else "")
        or f"post-{int(time.time())}"
    )
    slides_str = flatten_slides_to_strings(slides)
    post_text = f"{winner_variant['hook'].strip()}\n\n{CTA_POST_SUFFIX}\n\n{HASHTAGS}"

    return {
        "format": format_choice,
        "format_reason": format_reason,
        "topic": topic[:120],
        "slug": slug,
        "angle": angle.get("angle", ""),
        "visual_hook": angle.get("hook", ""),
        "hook_variants": variants,
        "hook_winner_formula": winner_formula,
        "hook_winner_reason": judge["reason"],
        "feed_hook": winner_variant["hook"],
        "slides": slides_str,
        "slides_structured": slides,
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
