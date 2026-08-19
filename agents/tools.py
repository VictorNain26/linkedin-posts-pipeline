"""
JSON Schemas pour les 8 tools utilisés par les agents Claude (tool_use forcé).

Pattern CCA-F D4 §3 : structured output via `tool_use` + JSON Schema strict
→ zéro parsing libre, validation syntaxique gratuite par l'API Anthropic.

Chaque tool est utilisé par 1 ou 2 agents :
- PAIN_TOOL          → agent 1 (Pain Excavator)
- ANGLE_TOOL         → agent 2 (Angle Scout)
- SLIDES_TOOL        → agents 3, 4, 5 (Slide Architect / Pen / Anti-AI Detector)
- VIOLATIONS_TOOL    → agent 5 (détection sémantique patterns IA)
- FACTUAL_CHECK_TOOL → agent 5b (cross-check faits slides vs article source)
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
    "description": "Submit the editorial angle and the visual hook of slide 1.",
    "input_schema": {
        "type": "object",
        "properties": {
            "angle": {"type": "string", "description": "Editorial angle in one sentence"},
            "hook": {"type": "string", "description": "Slide 1 hook, max 8 words", "maxLength": 80},
            "story_id": {
                "type": "string",
                "description": (
                    "REGISTRE 'preuve' uniquement : id de la story Victor choisie dans "
                    '<victor_stories_index>, ou "" si aucune ne colle naturellement. '
                    'Autres registres : "".'
                ),
            },
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
                        "kind": {
                            "type": "string",
                            "enum": ["standard", "list", "number"],
                            "description": (
                                "standard = phrase principale + développement. "
                                "list = titre (main) + 2-5 items courts (champ items) — pour les "
                                "checklists/questions, JAMAIS une liste écrasée dans sub. "
                                "number = UN chiffre marquant en très grand (main = le chiffre seul, "
                                "ex '2 M' ou '-40%', sub = ce qu'il signifie). "
                                "Vise 1 slide list OU number par carrousel quand la matière s'y prête."
                            ),
                        },
                        "main": {
                            "type": "string",
                            "description": (
                                "Main text, 1 idea, max 15 words. Mets en gras le ou les 1-2 mots "
                                "PIVOTS avec **mot** (rendu en bleu accent). Pour kind=number : "
                                "le chiffre seul, court."
                            ),
                        },
                        "sub": {"type": "string", "description": "Optional supporting line, can be empty"},
                        "items": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 90},
                            "maxItems": 5,
                            "description": "kind=list uniquement : 2-5 items courts (1 ligne chacun)",
                        },
                    },
                    "required": ["main"],
                },
            }
        },
        "required": ["slides"],
    },
}

TEXT_BODY_TOOL = {
    "name": "submit_text_body",
    "description": "Submit the body of a long-form LinkedIn text post (no carousel).",
    "input_schema": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "minLength": 1100,
                "maxLength": 2200,
                "description": (
                    "Corps du post text-only : 1300-2000 caractères, voix de Victor (vouvoiement), "
                    "phrases courtes, sauts de ligne fréquents (1-2 phrases par paragraphe, "
                    "style LinkedIn aéré). NE PAS inclure le hook d'ouverture (ajouté avant), "
                    "ni hashtags ni CTA final (ajoutés après). AUCUN markdown (**, _, #) : "
                    "LinkedIn affiche les astérisques littéralement."
                ),
            }
        },
        "required": ["body"],
    },
}

VIOLATIONS_TOOL = {
    "name": "submit_violations",
    "description": "Report AI-sounding patterns detected in the slides.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clean": {
                "type": "boolean",
                "description": "True if no AI patterns detected.",
            },
            "violations": {
                "type": "array",
                "items": {"type": "string", "description": "Verbatim phrase that sounds AI-generated."},
                "description": "Empty list if clean=true.",
            },
        },
        "required": ["clean", "violations"],
    },
}

FACTUAL_CHECK_TOOL = {
    "name": "submit_factual_check",
    "description": "Report facts/numbers in slides not supported by the source article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clean": {
                "type": "boolean",
                "description": "True if every fact in slides is traceable to the source article.",
            },
            "violations": {
                "type": "array",
                "items": {"type": "string", "description": "Specific claim not found in article."},
                "description": "Empty list if clean=true.",
            },
        },
        "required": ["clean", "violations"],
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
                        "body_lines": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {"type": "string", "maxLength": 140},
                            "description": (
                                "1-3 lignes courtes qui suivent le hook dans le body du post "
                                "(au-dessus du carrousel) : contexte ou tension, PAS un résumé "
                                "qui tue le swipe. La dernière peut teaser le carrousel. "
                                "Cohérentes avec CE hook précis."
                            ),
                        },
                    },
                    "required": ["formula", "hook", "body_lines"],
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
    "description": "Submit the first comment Victor posts under his own post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "comment": {
                "type": "string",
                "minLength": 60,
                "maxLength": 400,
                "description": (
                    "1er commentaire sous le post. Le contenu exact (pitch CTA ou complément "
                    "de valeur) est dicté par le prompt. AUCUN lien externe dans tous les cas "
                    "(LinkedIn pénalise -80% les commentaires avec URL en 2026)."
                ),
            }
        },
        "required": ["comment"],
    },
}
