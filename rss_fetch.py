"""
Veille RSS — fetch articles récents, enrich avec contenu, score avec Haiku.

Robustesse :
- Socket timeout par défaut sur feedparser
- Erreurs RSS isolées (1 source down != pipeline KO)
- Scoring via tool_use + JSON Schema strict
- Enrichissement HTTP : si summary vide, on fetch l'URL pour récup le contenu réel
  (évite que les agents inventent en partant de juste un titre)
"""

import json
import re
import socket
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser

import feedparser
import requests

from anthropic_client import call_tool
from config import (
    HAIKU_MODEL,
    RSS_ARTICLE_FETCH_TIMEOUT,
    RSS_ARTICLE_MAX_CHARS,
    RSS_FETCH_TIMEOUT,
    RSS_LOOKBACK_HOURS,
    RSS_SOURCES,
    TOKEN_BUDGETS,
)

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


class _HTMLStripper(HTMLParser):
    """Strip HTML tags to extract plain text from article body."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


def _strip_html(html: str) -> str:
    parser = _HTMLStripper()
    try:
        parser.feed(html)
    except Exception:
        # HTMLParser intolerant on some inputs — fallback regex
        return re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", parser.text()).strip()


def fetch_article_content(url: str) -> str:
    """Fetch URL et extract text content. Renvoie '' si fail (caller décide)."""
    if not url:
        return ""
    try:
        resp = requests.get(
            url,
            timeout=RSS_ARTICLE_FETCH_TIMEOUT,
            headers={"User-Agent": "linkedin-posts-pipeline/1.0 (+RSS enrichment)"},
        )
        if resp.status_code != 200:
            return ""
        text = _strip_html(resp.text)
        return text[:RSS_ARTICLE_MAX_CHARS]
    except (requests.RequestException, OSError) as e:
        print(f"[rss] article fetch failed for {url}: {e}", file=sys.stderr)
        return ""


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
                        continue
                    if pub_dt < cutoff:
                        continue
                items.append(
                    {
                        "title": entry.get("title", ""),
                        "summary": _strip_html(entry.get("summary") or entry.get("description") or "")[:500],
                        "url": entry.get("link", ""),
                        "source": (getattr(feed.feed, "title", None) or url),
                        "published": entry.get("published", ""),
                        # `content` rempli plus tard via fetch_article_content si besoin
                        "content": "",
                    }
                )
        except Exception as e:
            failures.append(f"{url}: {e}")
    if failures:
        print(f"[rss] {len(failures)} source(s) failed: " + " | ".join(failures), file=sys.stderr)
    if len(failures) >= len(RSS_SOURCES):
        print("[rss] WARNING: all sources failed", file=sys.stderr)
    return items


def enrich_with_article_content(items: list[dict], max_articles: int = 5) -> list[dict]:
    """Pour les items dont summary est vide ou trop court, WebFetch l'URL.

    On limite à max_articles pour éviter blowup latence + bandwidth.
    """
    enriched = 0
    for item in items:
        if enriched >= max_articles:
            break
        if len(item["summary"]) < 200 and item["url"]:
            content = fetch_article_content(item["url"])
            if content:
                item["content"] = content
                enriched += 1
                print(f"[rss] enriched {item['url'][:60]} ({len(content)} chars)", file=sys.stderr)
    return items


def score_relevance(items: list[dict]) -> list[tuple[int, dict]]:
    """Score 0-10 avec scoring strict orienté intégration IA (pas ML infra)."""
    if not items:
        return []
    articles_str = json.dumps(
        [
            {
                "i": i,
                "title": it["title"],
                "summary": (it["summary"] or it["content"])[:400],
                "source": it["source"],
            }
            for i, it in enumerate(items)
        ],
        ensure_ascii=False,
    )
    out = call_tool(
        model=HAIKU_MODEL,
        system=None,
        user_text=(
            "Tu scores la pertinence d'articles IA pour des posts LinkedIn de Victor Lenain, "
            "dev freelance Paris qui fait de l'INTÉGRATION IA dans des apps web "
            "(Claude/OpenAI/Mistral APIs, RAG, agents, MCP) pour PME et startups françaises.\n\n"
            "BARÈME STRICT :\n\n"
            "TRÈS PERTINENT (8-10) :\n"
            "- Lancement modèle/API grand public utilisable par un dev (Claude, GPT, Mistral, Gemini)\n"
            "- Nouveau SDK / framework / pattern pour intégrer l'IA (Anthropic SDK, MCP, LangChain, LlamaIndex)\n"
            "- Cas usage business IA concret pour PME (automatisation, RAG sur docs, agent métier)\n"
            "- Bonnes pratiques d'intégration IA (prompt caching, tool use, structured outputs)\n\n"
            "PERTINENT (5-7) :\n"
            "- Outils IA pour devs (Copilot, Cursor, Claude Code)\n"
            "- Comparaisons franches de modèles ou SDKs\n"
            "- Patterns d'agents et orchestration\n\n"
            "PAS PERTINENT (0-4) :\n"
            "- ML infrastructure lourde (training, GPU clusters, SageMaker, Trainium, fine-tuning custom)\n"
            "- Recherche académique pure (papers, benchmarks théoriques)\n"
            "- Hardware (puces, datacenters)\n"
            "- Levées de fonds, business des boites IA\n"
            "- Robotique, autonomous vehicles, vision biomedicale\n"
            "- Annonces produit grand public non développeur (consumer apps)\n\n"
            "Articles à scorer (ordre conservé) :\n" + articles_str + "\n\n"
            "Réponds 1 score par article, même ordre."
        ),
        tool=SCORE_TOOL,
        max_tokens=TOKEN_BUDGETS["rss_score"],
    )
    scores = out["scores"]
    if len(scores) != len(items):
        print(f"[rss] score count mismatch: {len(scores)} vs {len(items)}, padding", file=sys.stderr)
        if len(scores) < len(items):
            scores = scores + [5] * (len(items) - len(scores))
        else:
            scores = scores[: len(items)]
    return sorted(zip(scores, items, strict=False), key=lambda x: x[0], reverse=True)


def get_top_news(n: int = 5, min_score: int = 6) -> list[dict]:
    """Top N news pertinentes (score >= min_score), enrichies avec contenu article."""
    items = fetch_recent_items()
    if not items:
        print("[rss] no items fetched from any source", file=sys.stderr)
        return []

    # Enrichir le contenu AVANT scoring pour que Haiku ait du contexte réel
    items = enrich_with_article_content(items, max_articles=10)

    try:
        scored = score_relevance(items)
    except Exception as e:
        print(f"[rss] scoring failed: {e} — returning unscored top items", file=sys.stderr)
        return items[:n]

    relevant = [item for score, item in scored if score >= min_score][:n]
    if not relevant:
        print(
            f"[rss] {len(items)} items fetched but none scored >= {min_score} — nothing relevant today",
            file=sys.stderr,
        )
    return relevant


if __name__ == "__main__":
    news = get_top_news()
    print(json.dumps(news, ensure_ascii=False, indent=2))
    if not news:
        sys.exit(0)
