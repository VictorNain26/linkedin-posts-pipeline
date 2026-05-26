"""Core infrastructure — config, logging, Anthropic client."""

import anthropic_client
from config import (
    ANTI_AI_PATTERNS,
    DATA_DIR,
    DB_PATH,
    FORMAT_CAROUSEL,
    FORMAT_TEXT,
    LEARNINGS_PATH,
    LINKEDIN_API_VERSION,
    OUTPUT_DIR,
    RSS_SOURCES,
    STATE_DIR,
    system_voice,
)
from log import get_logger

__all__ = [
    "ANTI_AI_PATTERNS",
    "DATA_DIR",
    "DB_PATH",
    "FORMAT_CAROUSEL",
    "FORMAT_TEXT",
    "LEARNINGS_PATH",
    "LINKEDIN_API_VERSION",
    "OUTPUT_DIR",
    "RSS_SOURCES",
    "STATE_DIR",
    "anthropic_client",
    "get_logger",
    "system_voice",
]
