"""
Sous-package des agents Claude pour le pipeline LinkedIn.

Layout :
- tools.py   : JSON Schemas (PAIN_TOOL, ANGLE_TOOL, ...) pour tool_use forcé
- system.py  : injection des learnings hebdo dans le system block (_system_with_learnings)

Les fonctions des 8 agents (agent1_pain_excavator, agent2_angle_scout, ...) restent
dans generate_post.py pour l'instant — l'orchestration et les agents sont fortement
couplés (article_ctx, schemas partagés) et un split fin n'apporte pas de bénéfice net.

Tous les exports principaux sont re-exposés depuis ce __init__ pour permettre :
    from agents import PAIN_TOOL, _system_with_learnings
"""

from agents.system import _load_learnings_block, _system_with_learnings
from agents.tools import (
    ANGLE_TOOL,
    CTA_COMMENT_TOOL,
    HOOK_JUDGE_TOOL,
    HOOK_VARIANTS_TOOL,
    PAIN_TOOL,
    SLIDES_TOOL,
)

__all__ = [
    "ANGLE_TOOL",
    "CTA_COMMENT_TOOL",
    "HOOK_JUDGE_TOOL",
    "HOOK_VARIANTS_TOOL",
    "PAIN_TOOL",
    "SLIDES_TOOL",
    "_load_learnings_block",
    "_system_with_learnings",
]
