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


class TruncatedToolUseError(RuntimeError):
    """tool_use forcé tronqué par max_tokens : le JSON d'arguments est incomplet.
    Renvoyer ce dict partiel ferait sauter un KeyError opaque chez l'appelant —
    on lève explicitement pour que le budget soit corrigé ou la news suivante tentée."""


# Prix par million de tokens (USD) — Anthropic mai 2026
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"in": 0.80, "out": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}

# Accumulateur par run (reset par reset_run_usage())
_run_usage: list[dict] = []


def reset_run_usage() -> None:
    _run_usage.clear()


def get_run_cost_usd() -> float:
    total = 0.0
    for u in _run_usage:
        p = _PRICING.get(u["model"], _PRICING["claude-sonnet-4-6"])
        total += u.get("input_tokens", 0) * p["in"] / 1_000_000
        total += u.get("output_tokens", 0) * p["out"] / 1_000_000
        total += u.get("cache_creation_input_tokens", 0) * p["cache_write"] / 1_000_000
        total += u.get("cache_read_input_tokens", 0) * p["cache_read"] / 1_000_000
    return total


def get_run_usage_totals() -> dict:
    """Retourne les totaux de tokens + coût pour sauvegarde en DB."""
    total_in = total_out = total_cw = total_cr = 0
    for u in _run_usage:
        total_in += u.get("input_tokens", 0)
        total_out += u.get("output_tokens", 0)
        total_cw += u.get("cache_creation_input_tokens", 0)
        total_cr += u.get("cache_read_input_tokens", 0)
    return {
        "cost_usd": get_run_cost_usd(),
        "tokens_in": total_in,
        "tokens_out": total_out,
        "tokens_cache_write": total_cw,
        "tokens_cache_read": total_cr,
    }


def get_run_usage_summary() -> str:
    """Résumé texte des tokens utilisés + coût estimé."""
    t = get_run_usage_totals()
    return (
        f"tokens in={t['tokens_in']} out={t['tokens_out']} "
        f"cache_write={t['tokens_cache_write']} cache_read={t['tokens_cache_read']} "
        f"→ ${t['cost_usd']:.4f}"
    )


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
            # Track usage
            u = resp.usage
            entry = {
                "model": model,
                "tool": tool["name"],
                "input_tokens": getattr(u, "input_tokens", 0),
                "output_tokens": getattr(u, "output_tokens", 0),
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            }
            _run_usage.append(entry)
            p = _PRICING.get(model, _PRICING["claude-sonnet-4-6"])
            call_cost = (
                entry["input_tokens"] * p["in"] / 1_000_000
                + entry["output_tokens"] * p["out"] / 1_000_000
                + entry["cache_creation_input_tokens"] * p["cache_write"] / 1_000_000
                + entry["cache_read_input_tokens"] * p["cache_read"] / 1_000_000
            )
            logger.info(
                "[cost] %-30s %-25s in=%d out=%d cw=%d cr=%d → $%.4f",
                tool["name"],
                model,
                entry["input_tokens"],
                entry["output_tokens"],
                entry["cache_creation_input_tokens"],
                entry["cache_read_input_tokens"],
                call_cost,
            )

            if resp.stop_reason == "max_tokens":
                raise TruncatedToolUseError(
                    f"tool '{tool['name']}' tronqué à max_tokens={max_tokens} "
                    f"(out={entry['output_tokens']}) — augmente le budget de cet appel"
                )

            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                    return dict(block.input)
            raise RuntimeError(f"Claude did not call tool '{tool['name']}' — got: {resp.content!r}")
        except RateLimitError as e:
            last_err = e
            wait = ANTHROPIC_RETRY_BASE_DELAY * (2**attempt) * 2
            logger.warning("RateLimit attempt %d/%d, sleeping %ds", attempt + 1, ANTHROPIC_MAX_ATTEMPTS, wait)
            time.sleep(wait)
        except APIStatusError as e:
            last_err = e
            if e.status_code and 500 <= e.status_code < 600:
                wait = ANTHROPIC_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "HTTP %d attempt %d/%d, sleeping %ds",
                    e.status_code,
                    attempt + 1,
                    ANTHROPIC_MAX_ATTEMPTS,
                    wait,
                )
                time.sleep(wait)
            else:
                raise
        except APIError as e:
            last_err = e
            wait = ANTHROPIC_RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "APIError attempt %d/%d: %s, sleeping %ds", attempt + 1, ANTHROPIC_MAX_ATTEMPTS, e, wait
            )
            time.sleep(wait)
    raise RuntimeError(f"Anthropic call_tool failed after {ANTHROPIC_MAX_ATTEMPTS} attempts: {last_err}")
