"""Core infrastructure — config, logging, Anthropic client."""

from config import (
    DB_PATH,
    DATA_DIR,
    OUTPUT_DIR,
    STATE_DIR,
    LEARNINGS_PATH,
    RSS_SOURCES,
    ANTI_AI_PATTERNS,
    FORMAT_CAROUSEL,
    FORMAT_TEXT,
    LINKEDIN_API_VERSION,
    system_voice,
)
from log import get_logger
import anthropic_client

__all__ = [
    "DB_PATH",
    "DATA_DIR",
    "OUTPUT_DIR",
    "STATE_DIR",
    "LEARNINGS_PATH",
    "RSS_SOURCES",
    "ANTI_AI_PATTERNS",
    "FORMAT_CAROUSEL",
    "FORMAT_TEXT",
    "LINKEDIN_API_VERSION",
    "system_voice",
    "get_logger",
    "anthropic_client",
]
