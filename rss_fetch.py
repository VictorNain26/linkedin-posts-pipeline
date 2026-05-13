"""
Veille RSS — fetch articles récents, score pertinence avec Haiku (tool_use).

Robustesse :
- socket timeout par défaut sur feedparser (RSS_FETCH_TIMEOUT)
- Erreurs RSS isolées (1 source down ≠ pipeline KO)
- Scoring via tool_use + JSON Schema (pas de parse libre)
"""

import json
import socket
import sys
from datetime import datetime, timedelta

import feedparser

from anthropic_client import call_tool
from config import HAIKU_MODEL, RSS_FETCH_TIMEOUT, RSS_LOOKBACK_HOURS, RSS_SOURCES, TOKEN_BUDGETS

socket.setdefaulttimeout(RSS_FETCH_TIMEOUT)

SCORE_TOOL = {
    "name": "submit_scores",
    "description": "Submit relevance scores 0-10 for each article in the input order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 10},
            }
        },
        "required": ["scores"],
    },
}


def fetch_recent_items(hours: int = RSS_LOOKBACK_HOURS) -> list[dict]:
    items: list[dict] = []
    cutoff = datetime.now() - timedelta(hours=hours)
    failures: list[str] = []
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            if getattr(feed, "bozo", False) and not feed.entries:
                failures.append(f"{url}: parse error")
                continue
            for entry in feed.entries[:10]:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    try:
                        pub_dt = datetime(*published[:6])
                    except (TypeError, ValueError):
                        continue  # malformed RSS date, skip entry but keep source
                    if pub_dt < cutoff:
                        continue
                items.append(
                    {
                        "title": entry.get("title", ""),
                        "summary": (entry.get("summary") or "")[:500],
                        "url": entry.get("link", ""),
                        "source": (getattr(feed.feed, "title", None) or url),
                        "published": entry.get("published", ""),
                    }
                )
        except Exception as e:
            failures.append(f"{url}: {e}")
    if failures:
        print(f"[rss] {len(failures)} source(s) failed: " + " | ".join(failures), file=sys.stderr)
    if len(failures) >= len(RSS_SOURCES):
        print("[rss] WARNING: all sources failed", file=sys.stderr)
    return items


def score_relevance(items: list[dict]) -> list[tuple[int, dict]]:
    if not items:
        return []
    articles_str = json.dumps(
        [{"i": i, "title": it["title"], "summary": it["summary"][:200]} for i, it in enumerate(items)],
        ensure_ascii=False,
    )
    out = call_tool(
        model=HAIKU_MODEL,
        system=None,
        user_text=(
            "Score 0-10 la pertinence de chaque article pour des posts LinkedIn d'un dev freelance "
            "full-stack + intégration IA basé à Paris, qui cible PME / startups FR.\n\n"
            "Barème :\n"
            "- 8-10 : lancement modèle/API ou cas usage business IA concret\n"
            "- 5-7 : technique dev IA pertinente\n"
            "- 0-4 : hors sujet ou trop niche académique\n\n"
            "Articles (ordre conservé) :\n" + articles_str + "\n\n"
            "Réponds avec un score par article, dans le même ordre."
        ),
        tool=SCORE_TOOL,
        max_tokens=TOKEN_BUDGETS["rss_score"],
    )
    scores = out["scores"]
    # Align lengths defensively (tool_use schema doesn't enforce length match with input)
    if len(scores) != len(items):
        print(f"[rss] score count mismatch: {len(scores)} vs {len(items)}, padding", file=sys.stderr)
        if len(scores) < len(items):
            scores = scores + [5] * (len(items) - len(scores))
        else:
            scores = scores[: len(items)]
    return sorted(zip(scores, items, strict=False), key=lambda x: x[0], reverse=True)


def get_top_news(n: int = 5, min_score: int = 6) -> list[dict]:
    """Retourne jusqu'à N news triées par pertinence (score >= min_score).
    Liste vide si rien d'exploitable — le caller décide quoi faire (échec ou skip)."""
    items = fetch_recent_items()
    if not items:
        print("[rss] no items fetched from any source", file=sys.stderr)
        return []
    try:
        scored = score_relevance(items)
    except Exception as e:
        print(f"[rss] scoring failed: {e} — returning unscored top items", file=sys.stderr)
        return items[:n]
    relevant = [item for score, item in scored if score >= min_score][:n]
    if not relevant:
        print(
            f"[rss] {len(items)} items fetched but none scored ≥ {min_score} — nothing relevant today",
            file=sys.stderr,
        )
    return relevant


if __name__ == "__main__":
    news = get_top_news()
    # Sortie JSON même si vide : le caller (pipeline.sh / generate_post.py) décide.
    print(json.dumps(news, ensure_ascii=False, indent=2))
    if not news:
        sys.exit(0)  # pas d'erreur de fetch, juste rien de pertinent
