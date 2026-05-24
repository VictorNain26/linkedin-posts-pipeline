"""
Wrapper Anthropic SDK avec retry + prompt caching + tool_use forcé.

Patterns CCA-F appliqués :
- D5 §1b : prompt caching (cache_control ephemeral) sur system blocks stables
- D4 §3 : tool_use + JSON Schema = sortie structurée garantie syntaxiquement
- D4 §4 : retry-with-feedback (validation puis re-prompt avec erreur explicite)
- D1 §6b : retry+backoff exponentiel sur erreurs API transitoires
"""

import os
import time

from anthropic import Anthropic, APIError, APIStatusError, RateLimitError
from dotenv import load_dotenv

from config import ANTHROPIC_MAX_ATTEMPTS, ANTHROPIC_RETRY_BASE_DELAY
from log import get_logger

load_dotenv()

_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
logger = get_logger(__name__)


def call_tool(
    *,
    model: str,
    system: list[dict] | None,
    user_text: str,
    tool: dict,
    max_tokens: int,
) -> dict:
    """
    Force Claude à appeler `tool` et retourne le dict d'arguments validé syntaxiquement.

    tool: {"name": str, "description": str, "input_schema": dict (JSON Schema)}
    Retry exponentiel sur 429/5xx/timeout.
    Lève RuntimeError si Claude refuse d'appeler le tool (rare avec tool_choice forcé).
    """
    last_err: Exception | None = None
    for attempt in range(ANTHROPIC_MAX_ATTEMPTS):
        try:
            resp = _client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_text}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                    return dict(block.input)
            raise RuntimeError(f"Claude did not call tool '{tool['name']}' — got: {resp.content!r}")
        except RateLimitError as e:
            last_err = e
            wait = ANTHROPIC_RETRY_BASE_DELAY * (2 ** attempt) * 2
            logger.warning(
                "RateLimit attempt %d/%d, sleeping %ds", attempt + 1, ANTHROPIC_MAX_ATTEMPTS, wait
            )
            time.sleep(wait)
        except APIStatusError as e:
            last_err = e
            if e.status_code and 500 <= e.status_code < 600:
                wait = ANTHROPIC_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "HTTP %d attempt %d/%d, sleeping %ds",
                    e.status_code, attempt + 1, ANTHROPIC_MAX_ATTEMPTS, wait
                )
                time.sleep(wait)
            else:
                raise
        except APIError as e:
            last_err = e
            wait = ANTHROPIC_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "APIError attempt %d/%d: %s, sleeping %ds",
                attempt + 1, ANTHROPIC_MAX_ATTEMPTS, e, wait
            )
            time.sleep(wait)
    raise RuntimeError(f"Anthropic call_tool failed after {ANTHROPIC_MAX_ATTEMPTS} attempts: {last_err}")
