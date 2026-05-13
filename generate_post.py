"""
Pipeline 8 agents — génère slides carousel + hook + 1er commentaire LinkedIn.

Modes :
- evergreen (mardi) : cible PME, angle business prospect
- veille (jeudi)    : cible devs/CTOs, angle technique terrain
Mode auto-détecté via weekday, override possible via PIPELINE_MODE env var.

Agents :
  1. Pain Excavator        (Sonnet) — 3 douleurs prospect
  2. Angle Scout           (Sonnet) — angle contre-intuitif + hook visuel slide 1
  3. Slide Architect       (Sonnet) — structure les 7 slides
  4. Victor's Pen          (Sonnet) — réécrit dans la voix de Victor
  5. Anti-AI Detector      (Sonnet) — retry-with-feedback sur patterns interdits
  6. Hook Generator        (Sonnet) — 3 variations de hook texte
  7. Hook Judge            (Haiku)  — sélectionne le winner
  8. Engagement Comment    (Haiku)  — 1er commentaire (question + lien profil)

Patterns :
- tool_use + JSON Schema forcé sur tous les agents → 0 parsing libre
- Sonnet pour créativité, Haiku pour sélection/structure → -30% tokens
- retry-with-feedback sur Anti-AI Detector
- dédup keyword overlap par itération sur les news RSS
- 0 fallback silencieux : NoUsableNewsError si aucune news exploitable
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
    HASHTAGS_BY_MODE,
    HOOK_VARIATIONS_COUNT,
    KEYWORD_OVERLAP_THRESHOLD,
    MAX_DETECTOR_RETRIES,
    PROFILE_URL,
    SLIDE_COUNT,
    SONNET_MODEL,
    TOKEN_BUDGETS,
    current_mode,
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
    "description": f"Submit exactly {SLIDE_COUNT} carousel slides.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "minItems": SLIDE_COUNT,
                "maxItems": SLIDE_COUNT,
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
                            "enum": ["contrarian", "data", "narrative"],
                        },
                        "hook": {
                            "type": "string",
                            "minLength": 80,
                            "maxLength": 220,
                            "description": "150-200 chars. Voix orale. Pas de buzzword.",
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
            "winner_formula": {"type": "string", "enum": ["contrarian", "data", "narrative"]},
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["winner_formula", "reason"],
    },
}

ENGAGEMENT_COMMENT_TOOL = {
    "name": "submit_engagement_comment",
    "description": "Submit the first comment Victor posts under his own post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "comment": {
                "type": "string",
                "minLength": 60,
                "maxLength": 400,
                "description": "Une question ouverte qui invite à répondre + lien profil. Pas un teaser, du contenu de valeur.",
            }
        },
        "required": ["comment"],
    },
}


# ──────────────────────────────────────────────────────────────
# Agents
# ──────────────────────────────────────────────────────────────
def agent1_pain_excavator(topic: str, mode: str) -> list[str]:
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(mode),
        user_text=(
            f"Sujet : {topic}\n\n"
            "Identifie 3 VRAIES douleurs prospect derrière ce sujet. "
            "Reste fidèle à l'audience (cf. system). Concrètes, sans jargon."
        ),
        tool=PAIN_TOOL,
        max_tokens=TOKEN_BUDGETS["pain"],
    )
    return out["pains"]


def agent2_angle_scout(pains: list[str], mode: str) -> dict:
    return call_tool(
        model=SONNET_MODEL,
        system=system_voice(mode),
        user_text=(
            "Douleurs identifiées :\n- " + "\n- ".join(pains) + "\n\n"
            "Trouve l'angle contre-intuitif ou pattern-interrupt qui stoppe le scroll. "
            "L'angle doit surprendre, contredire une idée reçue, ou révéler quelque chose d'inattendu. "
            "Hook : première ligne du carousel (slide 1), max 8 mots, percutante."
        ),
        tool=ANGLE_TOOL,
        max_tokens=TOKEN_BUDGETS["angle"],
    )


def agent3_slide_architect(topic: str, angle: dict, mode: str) -> list[dict]:
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(mode),
        user_text=(
            f"Sujet : {topic}\n"
            f"Angle : {angle['angle']}\n"
            f"Hook (slide 1) : {angle['hook']}\n\n"
            f"Structure un carousel LinkedIn de {SLIDE_COUNT} slides.\n"
            "- Slide 1 : Hook/problème (commence par le hook fourni)\n"
            "- Slide 2 : Contexte / pourquoi maintenant\n"
            "- Slides 3-5 : 3 points clés (1 idée par slide, 1-2 phrases)\n"
            "- Slide 6 : Résultat / solution concrète\n"
            f"- Slide 7 : CTA — DOIT contenir '{CTA_SLIDE_TEXT}'\n"
            "Chaque slide = 1 idée max. main = 1 phrase punchy ; sub = développement court (optionnel)."
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["architect"],
    )
    return out["slides"]


def agent4_victors_pen(slides_outline: list[dict], mode: str) -> list[dict]:
    outline_str = "\n".join(
        f"Slide {i + 1} — main: {s['main']}" + (f" | sub: {s.get('sub', '')}" if s.get("sub") else "")
        for i, s in enumerate(slides_outline)
    )
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(mode),
        user_text=(
            "Réécris ce carousel dans la voix exacte de Victor (cf. règles dans le system).\n"
            "Préserve : la structure, l'angle, le hook slide 1, le CTA final.\n"
            "Modifie : le phrasé pour matcher la voix orale + courte de Victor.\n\n"
            "Outline :\n" + outline_str
        ),
        tool=SLIDES_TOOL,
        max_tokens=TOKEN_BUDGETS["pen"],
    )
    return out["slides"]


def _detect_violations(slides: list[dict]) -> list[str]:
    text = " ".join(s["main"] + " " + s.get("sub", "") for s in slides)
    return [p for p in ANTI_AI_PATTERNS if p in text]


def agent5_anti_ai_detector(slides: list[dict], mode: str) -> list[dict]:
    """Retry-with-feedback (CCA-F D4 §4) : on re-prompt avec les violations explicites."""
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
            system=system_voice(mode),
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


def agent6_hook_generator(topic: str, angle: dict, slides: list[dict], mode: str) -> list[dict]:
    """Génère 3 variations de hook texte du post (1 par formule).

    Best practice 2026 : drafter 3-7 hooks avant de choisir
    (cf. finallayer.com, leadsmonky.com — winning hook ≠ first idea).
    """
    slides_summary = " | ".join(s["main"] for s in slides[:4])
    out = call_tool(
        model=SONNET_MODEL,
        system=system_voice(mode),
        user_text=(
            f"Sujet : {topic}\n"
            f"Angle : {angle['angle']}\n"
            f"Hook visuel slide 1 : {angle['hook']}\n"
            f"Aperçu slides : {slides_summary}\n\n"
            "Écris 3 hooks pour le TEXTE du post LinkedIn (≈200 chars affichés avant See more).\n"
            "Ces hooks doivent être DIFFÉRENTS du hook visuel slide 1.\n\n"
            "1 hook par formule :\n"
            "- contrarian : challenge une idée reçue ('Tout le monde dit X. C'est faux.')\n"
            "- data       : statistique forte qui implique une histoire ('73% des PME qui Z…')\n"
            "- narrative  : démarrer en plein milieu d'une histoire ('Mardi dernier, un dirigeant m'a dit…')\n\n"
            "Contraintes par hook :\n"
            "- 150-200 caractères\n"
            "- 1 à 2 phrases courtes\n"
            "- Pas de buzzword (cf. interdits dans system)\n"
            "- Sonne comme Victor en oral, pas comme un titre marketing"
        ),
        tool=HOOK_VARIANTS_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_generator"],
    )
    return out["variants"]


def agent7_hook_judge(variants: list[dict], angle: dict, mode: str) -> dict:
    """Sélectionne le hook winner parmi les 3 variations.

    Critères : stoppe le scroll, ne sonne pas IA, tient sa promesse,
    matche l'audience du mode (PME vs dev).

    Modèle : Haiku 4.5 (sélection, pas génération créative → 67% moins cher
    que Sonnet sans perte de qualité sur ce type de tâche).
    """
    variants_str = "\n".join(f"[{v['formula']}] ({len(v['hook'])} chars) {v['hook']}" for v in variants)
    out = call_tool(
        model=HAIKU_MODEL,
        system=system_voice(mode),
        user_text=(
            f"Angle du post : {angle['angle']}\n\n"
            "3 variations de hook :\n" + variants_str + "\n\n"
            "Choisis LA meilleure pour l'audience définie en system.\n"
            "Critères de sélection :\n"
            "1. Stoppe le scroll (curiosité, promesse implicite forte)\n"
            "2. Sonne authentique, pas IA — pas de buzzword, voix Victor\n"
            "3. Tient sa promesse vis-à-vis du contenu du post (l'algo 2026 pénalise les hooks clickbait)\n"
            "4. Match l'audience (cf. system : PME prospect vs dev)\n\n"
            "Renvoie la formule winner + 1-2 phrases de justification."
        ),
        tool=HOOK_JUDGE_TOOL,
        max_tokens=TOKEN_BUDGETS["hook_judge"],
    )
    return out


def agent8_engagement_comment(topic: str, angle: dict, mode: str) -> str:
    """Écrit le 1er commentaire que Victor poste sous son propre post.

    Best practice 2026 : pas un teaser ni juste un lien (pénalisé). C'est de
    la valeur ajoutée — une question d'engagement + lien profil au passage.

    Modèle : Haiku 4.5 (commentaire court 200-400 chars, structure simple →
    67% moins cher que Sonnet, qualité équivalente).
    """
    out = call_tool(
        model=HAIKU_MODEL,
        system=system_voice(mode),
        user_text=(
            f"Sujet du post : {topic}\n"
            f"Angle : {angle['angle']}\n\n"
            "Écris le 1er commentaire que Victor poste sous son propre post.\n"
            "L'objectif : ouvrir la conversation, pas pousser un lien.\n\n"
            "Format attendu :\n"
            "1. Une mini-réflexion ou nuance qui prolonge le post (1-2 phrases).\n"
            "2. UNE question ouverte précise pour inviter les lecteurs à répondre.\n"
            f"3. Une mention discrète du profil : 'Pour creuser → {PROFILE_URL}'\n\n"
            "Contraintes : 200-400 chars, voix orale Victor, pas de buzzword."
        ),
        tool=ENGAGEMENT_COMMENT_TOOL,
        max_tokens=TOKEN_BUDGETS["comment_writer"],
    )
    return out["comment"]


# ──────────────────────────────────────────────────────────────
# Helpers
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


def ensure_cta(slides: list[dict]) -> list[dict]:
    """Garantit que la dernière slide contient le CTA."""
    if not slides:
        return slides
    last = slides[-1]
    combined = (last.get("main", "") + " " + last.get("sub", "")).lower()
    if "dm" not in combined and "discuter" not in combined:
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


def _normalize_news_input(topic_input) -> list[dict]:
    """Accepte : list[dict] (RSS output) | dict (1 news) | str (legacy)."""
    if topic_input is None:
        return []
    if isinstance(topic_input, list):
        return [n for n in topic_input if isinstance(n, dict)]
    if isinstance(topic_input, dict):
        return [topic_input]
    if isinstance(topic_input, str) and topic_input.strip():
        return [{"title": topic_input.strip(), "summary": "", "url": ""}]
    return []


def _run_once(topic: str, mode: str) -> tuple[list[dict], dict]:
    print(f"[mode={mode}] [agent1] Pain excavator…", file=sys.stderr)
    pains = agent1_pain_excavator(topic, mode)

    print("[agent2] Angle scout…", file=sys.stderr)
    angle = agent2_angle_scout(pains, mode)

    print("[agent3] Slide architect…", file=sys.stderr)
    outline = agent3_slide_architect(topic, angle, mode)

    print("[agent4] Victor's pen…", file=sys.stderr)
    draft = agent4_victors_pen(outline, mode)

    print("[agent5] Anti-AI detector…", file=sys.stderr)
    final = agent5_anti_ai_detector(draft, mode)
    return final, angle


def generate(topic_input=None, mode: str | None = None) -> dict:
    """
    Génère un post à partir d'une OU plusieurs news RSS.
    Aucun fallback silencieux : si aucune news n'est exploitable, raise NoUsableNewsError.
    """
    mode = current_mode(mode)
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

    for idx, news in enumerate(news_list):
        topic = _news_to_topic(news)
        print(f"[generate] trying news {idx + 1}/{len(news_list)}: {topic[:80]}", file=sys.stderr)
        try:
            slides, angle = _run_once(topic, mode)
        except Exception as e:
            last_error = f"news {idx}: {e}"
            print(f"[generate] run failed on news {idx + 1}: {e}", file=sys.stderr)
            continue

        keywords = extract_keywords(topic, slides)
        overlap = keyword_overlap_ratio(keywords)
        if overlap < KEYWORD_OVERLAP_THRESHOLD:
            print(f"[generate] news {idx + 1} accepted (overlap={overlap:.2f})", file=sys.stderr)
            break
        print(
            f"[dedup] news {idx + 1} overlap={overlap:.2f} ≥ {KEYWORD_OVERLAP_THRESHOLD}, trying next",
            file=sys.stderr,
        )
        last_error = f"news {idx} too similar to recent (overlap={overlap:.2f})"
    else:
        # Toutes les news ont overlap trop haut ou ont fail
        raise NoUsableNewsError(
            f"Aucune des {len(news_list)} news RSS n'est exploitable. Dernier motif : {last_error}"
        )

    slides = ensure_cta(slides)

    print("[agent6] Hook generator (3 variants)…", file=sys.stderr)
    variants = agent6_hook_generator(topic, angle, slides, mode)

    print("[agent7] Hook judge…", file=sys.stderr)
    judge = agent7_hook_judge(variants, angle, mode)
    winner_formula = judge["winner_formula"]
    winner_variant = next((v for v in variants if v["formula"] == winner_formula), variants[0])

    print("[agent8] Engagement comment writer…", file=sys.stderr)
    first_comment = agent8_engagement_comment(topic, angle, mode)

    # Décide du format pour cette publication (carousel / text / poll)
    format_choice, format_reason = select_format(mode)
    print(f"[format] {format_choice} — {format_reason}", file=sys.stderr)

    slug = (
        slugify(topic[:40])
        or (slugify(slides[0]["main"][:30]) if slides else "")
        or f"post-{int(time.time())}"
    )
    slides_str = flatten_slides_to_strings(slides)

    post_text = f"{winner_variant['hook'].strip()}\n\n{CTA_POST_SUFFIX}\n\n{HASHTAGS_BY_MODE[mode]}"

    return {
        "mode": mode,
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
# Echec explicite si stdin vide, JSON invalide, ou liste vide.
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if sys.stdin.isatty():
        print(
            "[generate] ERROR: no stdin. This script reads RSS news (JSON list) from stdin. "
            "Usage: python rss_fetch.py | python generate_post.py",
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
