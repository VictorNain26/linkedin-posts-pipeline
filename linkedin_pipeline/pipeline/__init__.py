"""Pipeline — génération de posts, RSS, sélection format, rapport hebdo."""

import generate_post
import rss_fetch
import format_selector
import weekly_report

from format_selector import select_format
from rss_fetch import fetch_recent_items, score_relevance

__all__ = [
    "generate_post",
    "rss_fetch",
    "format_selector",
    "weekly_report",
    "select_format",
    "fetch_recent_items",
    "score_relevance",
]
