"""
Logging centralisé pour tout le pipeline.

Usage dans les modules :
    from log import get_logger
    logger = get_logger(__name__)
    logger.info("RSS items fetched: %d", count)
    logger.warning("scoring failed, returning unscored")
    logger.error("Anthropic API failed after retries: %s", err)

Format de sortie (stderr) :
    [2026-05-24T22:00:01] [rss_fetch] INFO  RSS items fetched: 5
    [2026-05-24T22:00:02] [generate_post] WARNING  hook count mismatch

Configuration via env vars :
    LOG_LEVEL   = DEBUG | INFO (default) | WARNING | ERROR
    LOG_FORMAT  = "plain" (default) | "json" (pour ingestion structured logging)
"""

import json as _json
import logging
import os
import sys
from datetime import datetime

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_FORMAT = os.environ.get("LOG_FORMAT", "plain").lower()

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
if _LOG_LEVEL not in _VALID_LEVELS:
    _LOG_LEVEL = "INFO"


class _PlainFormatter(logging.Formatter):
    """Format human-readable : [timestamp] [module] LEVEL message"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).isoformat(timespec="seconds")
        # Tronque le module name (linkedin_pipeline.agents.foo → foo)
        module = record.name.rsplit(".", 1)[-1]
        msg = record.getMessage()
        return f"[{ts}] [{module}] {record.levelname:<8} {msg}"


class _JsonFormatter(logging.Formatter):
    """Format machine-readable JSON line — pour ingestion dans des outils
    (Loki/Datadog/CloudWatch). Champs : ts, module, level, message, [exc_info]."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "module": record.name.rsplit(".", 1)[-1],
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def _build_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    formatter = _JsonFormatter() if _LOG_FORMAT == "json" else _PlainFormatter()
    handler.setFormatter(formatter)
    return handler


# Setup root logger une seule fois — modules dérivent via get_logger().
_root = logging.getLogger("linkedin_pipeline")
if not _root.handlers:
    _root.addHandler(_build_handler())
    _root.setLevel(getattr(logging, _LOG_LEVEL))
    _root.propagate = False  # évite double-print si le caller a aussi configuré root


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger nommé par module. Hérite de la config root."""
    # Normalise : "config" → "linkedin_pipeline.config"
    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"linkedin_pipeline.{short}")
