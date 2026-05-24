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
    except (AssertionError, ValueError, UnicodeDecodeError):
        # HTMLParser intolerant on malformed input — fallback regex
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
        except (requests.RequestException, OSError, AttributeError, ValueError) as e:
            # Network, feedparser malformé, ou source RSS down — on isole, on continue avec les autres
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
    """Score 0-10. Barème aligné cible PME/CTO non-tech (cf. AUDIENCE_DESC dans config.py).

    Priorise les sujets qui ont un angle business activable pour Victor :
    impacts business des annonces IA, conformité (CNIL, AI Act), ROI/cas d'usage
    entreprise, adoption IA en PME. Écarte le tech-pour-tech sans angle business.
    """
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
        system=[
            {
                "type": "text",
                "text": (
                    "<role>\n"
                    "Tu scores la pertinence d'articles pour le compte LinkedIn de Victor Lenain, "
                    "dev freelance + intégrateur IA Paris.\n"
                    "</role>\n\n"
                    "<audience>\n"
                    "Dirigeants de PME et CTOs français qui envisagent d'intégrer l'IA. "
                    "MAJORITÉ NON TECHNIQUE. Vocabulaire BUSINESS. Sensibles : ROI, time-to-value, "
                    "risques (coût, lock-in, hallucinations, dépendance fournisseur, "
                    "conformité RGPD/AI Act, équipe pas formée).\n"
                    "</audience>\n\n"
                    "<task>\n"
                    "Score chaque article selon sa capacité à devenir un post LinkedIn business "
                    "ACTIONNABLE pour cette audience. Sois STRICT — préfère 5 articles bien scorés "
                    "à 20 mal scorés.\n"
                    "</task>"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        user_text=(
            "<scoring_rubric>\n\n"
            "<tier score=\"8-10\" label=\"TRÈS PERTINENT\">\n"
            "- Conformité / régulation IA (CNIL, EU AI Act, RGPD + IA, certifications)\n"
            "- ROI / cas d'usage IA concret en entreprise AVEC chiffres ou bénéfices nommés\n"
            "- Annonces produit IA majeures (OpenAI, Anthropic, Mistral) MAIS uniquement si l'article expose des IMPLICATIONS business directes pour une PME (pas juste tech-product)\n"
            "- Adoption IA en PME française ou européenne, retours terrain\n"
            "- Risques IA pour les entreprises (sécu, lock-in, biais, dépendance fournisseur)\n"
            "</tier>\n\n"
            "<tier score=\"5-7\" label=\"PERTINENT\">\n"
            "- Annonces produit IA généralistes (besoin de pivot business par Victor pour activer)\n"
            "- Stratégie IA en entreprise (organisation, recrutement, gouvernance)\n"
            "- Comparaisons d'outils IA grand public\n"
            "- Études / rapports IA business (McKinsey, BCG, Gartner) si chiffres exploitables\n"
            "</tier>\n\n"
            "<tier score=\"0-4\" label=\"PAS PERTINENT\">\n"
            "- Tech infrastructure pure (training, GPU clusters, fine-tuning) sans angle business\n"
            "- Recherche académique, benchmarks théoriques, papers sans application directe\n"
            "- Hardware (puces, datacenters)\n"
            "- Levées de fonds isolées sans angle stratégique pour la cible\n"
            "- Robotique, autonomous vehicles, vision biomédicale (hors scope)\n"
            "- Apps grand public B2C (ChatGPT side-features) sans angle B2B\n"
            "- Sujets non-IA (politique, économie générale) — sauf CNIL/régulation IA\n"
            "</tier>\n\n"
            "</scoring_rubric>\n\n"
            "<critical_trap>\n"
            "Un article OpenAI/Anthropic n'est PAS automatiquement à 8-10. Test rapide :\n"
            "\"Un dirigeant PME français non-tech peut-il tirer une décision concrète après lecture ?\"\n"
            "Si non → 5-7 max.\n"
            "</critical_trap>\n\n"
            "<calibration_examples>\n"
            "<example score=\"9\">\n"
            "  Titre: \"La CNIL publie ses recommandations sur l'IA générative pour les RH\"\n"
            "  → Conformité IA + cible RH PME directement. Décision claire après lecture.\n"
            "</example>\n"
            "<example score=\"8\">\n"
            "  Titre: \"L'EU AI Act entre en vigueur : ce que ça change pour les PME\"\n"
            "  → Régulation impactant directement la cible. Très actionnable.\n"
            "</example>\n"
            "<example score=\"7\">\n"
            "  Titre: \"How Virgin Atlantic ships faster with Codex\"\n"
            "  → Cas business avec ROI nommé mais cible Virgin = grande entreprise tech.\n"
            "  Pivot possible vers PME (\"que peuvent en tirer les PME ?\").\n"
            "</example>\n"
            "<example score=\"6\">\n"
            "  Titre: \"OpenAI named a Leader by Gartner in coding agents\"\n"
            "  → Annonce produit nécessitant un pivot fort. Pas immédiatement actionnable.\n"
            "</example>\n"
            "<example score=\"5\">\n"
            "  Titre: \"Une startup française lève 50M pour son agent IA\"\n"
            "  → Levée. Pivot possible (\"que faut-il regarder côté outils ?\") mais limité.\n"
            "</example>\n"
            "<example score=\"3\">\n"
            "  Titre: \"OpenAI partners with Dell on hybrid Codex deployment\"\n"
            "  → Partnership infra entre grandes boites. Pas pertinent PME.\n"
            "</example>\n"
            "<example score=\"2\">\n"
            "  Titre: \"An OpenAI model has disproved a discrete geometry conjecture\"\n"
            "  → Recherche académique pure. Aucune décision PME possible.\n"
            "</example>\n"
            "<example score=\"1\">\n"
            "  Titre: \"Macron annonce une rallonge budgétaire pour la défense\"\n"
            "  → Hors scope IA business.\n"
            "</example>\n"
            "</calibration_examples>\n\n"
            "<articles_to_score>\n"
            + articles_str + "\n"
            "</articles_to_score>\n\n"
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
    except (RuntimeError, KeyError, ValueError) as e:
        # Anthropic API down / schema mismatch / malformed response → fallback non-scored
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
