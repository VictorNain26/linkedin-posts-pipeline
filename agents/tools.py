"""
JSON Schemas pour les 6 tools utilisés par les agents Claude (tool_use forcé).

Pattern CCA-F D4 §3 : structured output via `tool_use` + JSON Schema strict
→ zéro parsing libre, validation syntaxique gratuite par l'API Anthropic.

Chaque tool est utilisé par 1 ou 2 agents :
- PAIN_TOOL          → agent 1 (Pain Excavator)
- ANGLE_TOOL         → agent 2 (Angle Scout)
- SLIDES_TOOL        → agents 3, 4, 5 (Slide Architect / Pen / Anti-AI Detector)
- HOOK_VARIANTS_TOOL → agent 6 (Hook Generator)
- HOOK_JUDGE_TOOL    → agent 7 (Hook Judge)
- CTA_COMMENT_TOOL   → agent 8 (CTA Comment)
"""

from config import (
    HOOK_VARIATIONS_COUNT,
    SLIDE_COUNT_MAX,
    SLIDE_COUNT_MIN,
    SLIDE_COUNT_TARGET,
)

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
                            "maxLength": 210,
                            "description": (
                                "CIBLE : 100-140 chars (cutoff mobile = 80%+ du trafic 2026). "
                                "Hard limit : 210 chars (cutoff desktop). "
                                "Voix orale. Pas de buzzword. AUCUN détail inventé. "
                                "PAS de template anglais reconnaissable type 'Here's what nobody tells you' "
                                "(360Brew détecte sémantiquement les hooks copy-paste depuis mars 2026)."
                            ),
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
                    "CTA direct + bénéfice clair pour le prospect + canal d'action 'DM ouvert'. "
                    "AUCUN lien externe (LinkedIn pénalise -80% les commentaires avec URL en 2026). "
                    "PAS une question d'engagement, c'est une invitation à agir."
                ),
            }
        },
        "required": ["comment"],
    },
}
