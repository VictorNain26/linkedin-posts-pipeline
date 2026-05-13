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

from anthropic_client import call_tool
from config import (
    ANTI_AI_PATTERNS,
    CTA_POST_SUFFIX,
    CTA_SLIDE_TEXT,
    HAIKU_MODEL,
    HASHTAGS,
    HOOK_VARIATIONS_COUNT,
    KEYWORD_OVERLAP_THRESHOLD,
    MAX_DETECTOR_RETRIES,
    PROFILE_URL,
    SLIDE_COUNT_MAX,
    SLIDE_COUNT_MIN,
    SLIDE_COUNT_TARGET,
    SONNET_MODEL,
    TOKEN_BUDGETS,
    system_voice,
)
from format_selector import select_format
from history import keyword_overlap_ratio

# ──────────────────────────────────────────────────────────────
# JSON Schemas (CCA-F D4 §3)
# ──────────────────────────────────────────────────────────────
PAIN_TOOL = {
    "name": "submit_pains",
    "description": "Submit 3 prospect pain formulations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pains": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string", "minLength": 10},
            }
        },
        "required": ["pains"],
    },
}

ANGLE_TOOL = {
    "name": "submit_angle",
    "description": "Submit the contrarian angle and the visual hook of slide 1.",
    "input_schema": {
        "type": "object",
        "properties": {
            "angle": {"type": "string", "description": "Contrarian angle in one sentence"},
            "hook": {"type": "string", "description": "Slide 1 hook, max 8 words", "maxLength": 80},
        },
        "required": ["angle", "hook"],
    },
}

SLIDES_TOOL = {
    "name": "submit_slides",
    "description": (
        f"Submit between {SLIDE_COUNT_MIN} and {SLIDE_COUNT_MAX} carousel slides. "
        f"Target sweet spot: {SLIDE_COUNT_TARGET}. Use only what the content needs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "minItems": SLIDE_COUNT_MIN,
                "maxItems": SLIDE_COUNT_MAX,
                "items": {
                    "type": "object",
                    "properties": {
                        "main": {
                            "type": "string",
                            "description": "Main text of the slide, 1 idea, max 15 words",
                        },
                        "sub": {"type": "string", "description": "Optional supporting line, can be empty"},
                    },
                    "required": ["main"],
                },
            }
        },
        "required": ["slides"],
    },
}

HOOK_VARIANTS_TOOL = {
    "name": "submit_hook_variants",
    "description": "Submit 3 LinkedIn feed hook variations (1 per formula).",
    "input_schema": {
        "type": "object",
        "properties": {
            "variants": {
                "type": "array",
                "minItems": HOOK_VARIATIONS_COUNT,
                "maxItems": HOOK_VARIATIONS_COUNT,
                "items": {
                    "type": "object",
                    "properties": {
                        "formula": {
                            "type": "string",
                            "enum": ["contrarian", "data", "prospect_question"],
                        },
                        "hook": {
                            "type": "string",
                            "minLength": 80,
                            "maxLength": 220,
                            "description": "150-200 chars. Voix orale. Pas de buzzword. AUCUN détail inventé.",
                        },
                    },
                    "required": ["formula", "hook"],
                },
            }
        },
        "required": ["variants"],
    },
}

HOOK_JUDGE_TOOL = {
    "name": "submit_hook_winner",
    "description": "Pick the winning hook formula with a short justification.",
    "input_schema": {
        "type": "object",
        "properties": {
            "winner_formula": {"type": "string", "enum": ["contrarian", "data", "prospect_question"]},
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["winner_formula", "reason"],
    },
}

CTA_COMMENT_TOOL = {
    "name": "submit_cta_comment",
    "description": "Submit the first comment as a CTA Victor posts under his own post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "comment": {
                "type": "string",
                "minLength": 60,
                "maxLength": 400,
                "description": (
                    "CTA direct + bénéfice clair pour le prospect + lien d'action "
                    "(victorlenain.fr ou DM). PAS une question d'engagement, c'est une invitation à agir."
                ),
            }
        },
        "required": ["comment"],
    },
}


# ──────────────────────────────────────────────────────────────
# Agents
# ──────────────────────────────────────────────────────────────
def agent1_pain_excavator(article_ctx: str) -> list[str]:
    """Identifie 3 douleurs RÉELLES du prospect, à la lecture de l'article."""
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(),
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
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            "Douleurs prospect identifiées :\n- " + "\n- ".join(pains) + "\n\n"
            "Trouve l'angle éditorial qui :\n"
            "1. COMMENTE l'article (pas raconte une histoire perso fictive)\n"
            "2. PARLE aux douleurs ci-dessus du décideur\n"
            "3. SURPREND ou contredit une idée reçue largement répandue dans l'audience business\n\n"
            "Hook visuel : première ligne du carrousel (slide 1), max 8 mots, percutante. "
            "Doit interpeller le décideur, PAS raconter Victor."
        ),
        tool=ANGLE_TOOL,
        max_tokens=TOKEN_BUDGETS["angle"],
    )


def agent3_slide_architect(article_ctx: str, angle: dict) -> list[dict]:
    """Structure les slides en commentant l'article pour le décideur."""
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            f"Angle retenu : {angle['angle']}\n"
            f"Hook slide 1 : {angle['hook']}\n\n"
            f"Structure un carrousel LinkedIn entre {SLIDE_COUNT_MIN} et {SLIDE_COUNT_MAX} slides "
            f"(cible idéale : {SLIDE_COUNT_TARGET}) QUI COMMENTE L'ARTICLE pour le décideur.\n\n"
            "Le nombre de slides dépend du contenu :\n"
            f"- {SLIDE_COUNT_MIN}-6 slides si le sujet est simple\n"
            "- 7-8 slides pour un sujet riche avec plusieurs implications\n"
            f"- 9-{SLIDE_COUNT_MAX} slides UNIQUEMENT si vraiment nécessaire\n\n"
            "Structure type :\n"
            "- Slide 1 : Hook visuel (utilise exactement la phrase fournie)\n"
            "- Slide 2 : Ce que l'article annonce, en 1 phrase clé (résumé factuel sans invention)\n"
            "- Slides intermédiaires : implications BUSINESS pour le décideur "
            "(ROI, coût, risque, équipe, conformité)\n"
            "- Avant-dernière slide : Recommandation actionnable (cadre de décision)\n"
            f"- DERNIÈRE slide : CTA — DOIT contenir '{CTA_SLIDE_TEXT}'\n\n"
            "Chaque slide = 1 idée. main = phrase punchy ; sub = développement court (optionnel).\n"
            "Privilégie un carrousel COURT et DENSE plutôt que long et délayé.\n"
            "INTERDIT : chiffres inventés, anecdotes perso, situations fictives. "
            "Si un point manque de fact, retire la slide."
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
        model=SONNET_MODEL,
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            "Réécris ce carrousel dans la voix exacte de Victor (cf. règles voix dans system).\n\n"
            "PRÉSERVE :\n"
            "- la structure des slides (même nombre, même ordre)\n"
            "- l'angle, le hook slide 1, le CTA final\n"
            "- les faits de l'article source (rien d'autre comme source factuelle)\n\n"
            "MODIFIE :\n"
            "- le phrasé pour matcher la voix orale + courte de Victor\n\n"
            "INTERDIT : ajouter chiffres, anecdotes, situations qui ne sont pas dans l'outline "
            "ou l'article. Si un point manque de fact, garde-le tel quel ou enlève-le. "
            "N'invente PAS de détail pour rendre crédible.\n\n"
            "Outline à réécrire :\n" + outline_str
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["pen"],
    )
    return out["slides"]


def _detect_violations(slides: list[dict]) -> list[str]:
    text = " ".join(s["main"] + " " + s.get("sub", "") for s in slides)
    return [p for p in ANTI_AI_PATTERNS if p in text]


def agent5_anti_ai_detector(slides: list[dict]) -> list[dict]:
    """Retry-with-feedback (CCA-F D4 §4) : re-prompt avec les violations explicites."""
    current = slides
    for attempt in range(MAX_DETECTOR_RETRIES + 1):
        violations = _detect_violations(current)
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
            system=system_voice(),
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


def agent6_hook_generator(article_ctx: str, angle: dict, slides: list[dict]) -> list[dict]:
    """Génère 3 variations de hook texte (1 par formule)."""
    slides_summary = " | ".join(s["main"] for s in slides[:4])
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            f"Angle : {angle['angle']}\n"
            f"Hook visuel slide 1 : {angle['hook']}\n"
            f"Aperçu slides : {slides_summary}\n\n"
            "Écris 3 hooks pour le TEXTE du post LinkedIn (≈200 chars affichés avant See more).\n"
            "Ces hooks doivent être DIFFÉRENTS du hook visuel slide 1.\n\n"
            "1 hook par formule (TOUTES orientées prospect, AUCUNE anecdote perso inventée) :\n\n"
            "- contrarian       : challenge une idée reçue du marché ou de l'article.\n"
            "                     Ex : 'Tout le monde pense que X. L'annonce d'hier dit l'inverse.'\n\n"
            "- data             : cite UN chiffre PRÉSENT dans l'article + son implication.\n"
            "                     SI l'article n'a pas de chiffre exploitable, n'utilise PAS cette formule "
            "                     (laisse vide ou propose une variante du contrarian).\n"
            "                     N'invente JAMAIS un chiffre précis (pas de '73%' ou 'McKinsey' sortis de nulle part).\n\n"
            "- prospect_question : pose une question qui résonne avec une douleur du prospect.\n"
            "                     Ex : 'Tu hésites encore avec X pour ton projet IA ? L'annonce d'hier "
            "                     pourrait te faire reconsidérer.'\n\n"
            "Contraintes par hook :\n"
            "- 150-200 caractères\n"
            "- 1 à 2 phrases courtes\n"
            "- Pas de buzzword (cf. interdits dans system)\n"
            "- Parle au LECTEUR (tu/vous), pas de 'Mardi dernier j'ai...' fictif\n"
            "- Sonne oral, pas titre marketing"
        ),
        tool=HOOK_VARIANTS_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_generator"],
    )
    return out["variants"]


def agent7_hook_judge(article_ctx: str, variants: list[dict], angle: dict) -> dict:
    """Sélectionne le hook winner parmi les 3 variations (Haiku)."""
    variants_str = "\n".join(f"[{v['formula']}] ({len(v['hook'])} chars) {v['hook']}" for v in variants)
    out = call_tool(
        model=HAIKU_MODEL,
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            f"Angle du post : {angle['angle']}\n\n"
            "3 variations de hook :\n" + variants_str + "\n\n"
            "Choisis LA meilleure pour l'audience définie en system.\n"
            "Critères de sélection :\n"
            "1. STOPPE le scroll (curiosité, promesse implicite forte)\n"
            "2. AUCUNE invention factuelle : si un hook cite un chiffre précis, ce chiffre DOIT venir "
            "   de l'article source. Si tu détectes un chiffre fabriqué, RECALE ce hook.\n"
            "3. PARLE au lecteur (pas anecdote perso fictive type 'Mardi dernier j'ai...'). "
            "   Si un hook raconte la vie de Victor de façon non sourcée, RECALE-le.\n"
            "4. TIENT sa promesse vis-à-vis du contenu du post (l'algo 2026 pénalise les hooks clickbait)\n"
            "5. MATCH l'audience décideur PME (pas trop technique)\n\n"
            "Renvoie la formule winner + 1-2 phrases de justification."
        ),
        tool=HOOK_JUDGE_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_judge"],
    )
    return out


def agent8_cta_comment(article_ctx: str, angle: dict) -> str:
    """1er commentaire = CTA direct sous le post (Haiku)."""
    out = call_tool(
        model=HAIKU_MODEL,
        system=system_voice(),
        user_text=(
            f"{article_ctx}\n\n"
            f"Angle du post : {angle['angle']}\n\n"
            "Écris le 1er commentaire que Victor poste sous son propre post = un CTA DIRECT.\n\n"
            "Objectif : pousser le prospect à AGIR (pas juste réagir).\n\n"
            "Format attendu (200-400 chars) :\n"
            "1. UNE phrase de transition courte vers l'action (ancrée sur le sujet du post).\n"
            "2. LE CTA explicite : ce que tu PROPOSES (audit gratuit, appel découverte, "
            "   ressource concrète, sparring 30min, etc).\n"
            "3. LE BÉNÉFICE clair pour le lecteur (ce qu'il gagne : économie, clarté, gain de temps).\n"
            f"4. LE LIEN d'action : {PROFILE_URL} OU 'DM ouvert'.\n\n"
            "Ton : direct, voix Victor (oral, courte), pas vendeur agressif.\n"
            "INTERDIT : anecdote perso fictive, chiffre inventé, promesse exagérée."
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
    print("[agent5] Anti-AI detector…", file=sys.stderr)
    final = agent5_anti_ai_detector(draft)
    return final, angle


def generate(topic_input=None) -> dict:
    """
    Génère un post à partir d'une OU plusieurs news RSS.
    Aucun fallback silencieux : si aucune news n'est exploitable, raise NoUsableNewsError.
    """
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

    for idx, news in enumerate(news_list):
        topic = _news_to_topic(news)
        article_ctx = _article_context(news)
        print(f"[generate] trying news {idx + 1}/{len(news_list)}: {topic[:80]}", file=sys.stderr)
        try:
            slides, angle = _run_once(article_ctx)
        except Exception as e:
            last_error = f"news {idx}: {e}"
            print(f"[generate] run failed on news {idx + 1}: {e}", file=sys.stderr)
            continue

        keywords = extract_keywords(topic, slides)
        overlap = keyword_overlap_ratio(keywords)
        if overlap < KEYWORD_OVERLAP_THRESHOLD:
            print(f"[generate] news {idx + 1} accepted (overlap={overlap:.2f})", file=sys.stderr)
            article_ctx_winner = article_ctx
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
    judge = agent7_hook_judge(article_ctx_winner, variants, angle)
    winner_formula = judge["winner_formula"]
    winner_variant = next((v for v in variants if v["formula"] == winner_formula), variants[0])

    print("[agent8] CTA comment writer…", file=sys.stderr)
    first_comment = agent8_cta_comment(article_ctx_winner, angle)

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
