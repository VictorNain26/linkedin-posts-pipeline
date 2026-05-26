"""Pipeline — génération de posts, RSS, sélection format, rapport hebdo."""

import format_selector
import generate_post
import rss_fetch
import weekly_report
from format_selector import select_format
from rss_fetch import fetch_recent_items, score_relevance

__all__ = [
    "fetch_recent_items",
    "format_selector",
    "generate_post",
    "rss_fetch",
    "score_relevance",
    "select_format",
    "weekly_report",
]
