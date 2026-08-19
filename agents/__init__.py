"""
Sous-package des agents Claude pour le pipeline LinkedIn.

Layout :
- tools.py   : JSON Schemas (PAIN_TOOL, ANGLE_TOOL, ...) pour tool_use forcé
- system.py  : injection des learnings hebdo dans le system block (_system_with_learnings)
- stories.py : banque d'anecdotes réelles de Victor (registre "preuve")

Les fonctions des agents (agent1_pain_excavator, agent2_angle_scout, ...) restent
dans generate_post.py pour l'instant — l'orchestration et les agents sont fortement
couplés (article_ctx, schemas partagés) et un split fin n'apporte pas de bénéfice net.

Tous les exports principaux sont re-exposés depuis ce __init__ pour permettre :
    from agents import PAIN_TOOL, _system_with_learnings
"""

from agents.stories import get_story, load_stories, stories_index_block, story_block
from agents.system import _load_learnings_block, _system_with_learnings
from agents.tools import (
    ANGLE_TOOL,
    CTA_COMMENT_TOOL,
    FACTUAL_CHECK_TOOL,
    HOOK_JUDGE_TOOL,
    HOOK_VARIANTS_TOOL,
    PAIN_TOOL,
    SLIDES_TOOL,
    TEXT_BODY_TOOL,
    VIOLATIONS_TOOL,
)

__all__ = [
    "ANGLE_TOOL",
    "CTA_COMMENT_TOOL",
    "FACTUAL_CHECK_TOOL",
    "HOOK_JUDGE_TOOL",
    "HOOK_VARIANTS_TOOL",
    "PAIN_TOOL",
    "SLIDES_TOOL",
    "TEXT_BODY_TOOL",
    "VIOLATIONS_TOOL",
    "_load_learnings_block",
    "_system_with_learnings",
    "get_story",
    "load_stories",
    "stories_index_block",
    "story_block",
]
