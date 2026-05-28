"""
Veille RSS — fetch articles récents, enrich avec contenu, score avec Haiku.

Robustesse :
- Socket timeout par défaut sur feedparser
- Erreurs RSS isolées (1 source down != pipeline KO)
- Scoring via tool_use + JSON Schema strict (sur titre + résumé RSS, sans fetch HTTP)
- Extraction du corps complet (trafilatura) faite UNIQUEMENT sur l'article retenu,
  côté generate_post — pas de fetch en masse avant scoring (économie latence + bande passante)
"""

import json
import re
import socket
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser

import feedparser
import requests
import trafilatura

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


def _fetch_html(url: str) -> str:
    """Récupère le HTML brut d'une URL. Renvoie '' si fail."""
    try:
        resp = requests.get(
            url,
            timeout=RSS_ARTICLE_FETCH_TIMEOUT,
            headers={"User-Agent": "linkedin-posts-pipeline/1.0 (+RSS enrichment)"},
        )
        if resp.status_code != 200:
            return ""
        return resp.text
    except (requests.RequestException, OSError) as e:
        print(f"[rss] article fetch failed for {url}: {e}", file=sys.stderr)
        return ""


def fetch_article_text(url: str) -> str:
    """Extrait le corps propre d'un article (trafilatura : sans nav/cookies/sponsor).

    Fallback sur un strip HTML basique si trafilatura échoue. Renvoie '' si tout échoue
    (le caller décide : _article_context bascule alors sur le résumé RSS).
    """
    if not url:
        return ""
    html = _fetch_html(url)
    if not html:
        return ""
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    text = extracted or _strip_html(html)
    return text[:RSS_ARTICLE_MAX_CHARS]


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
                        # `content` rempli côté generate_post via fetch_article_text (article retenu only)
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


def score_relevance(items: list[dict], recent_topics: list[str] | None = None) -> list[tuple[int, dict]]:
    """Score 0-10 chaque article pour la cible PME/CTO non-tech.

    Deux exigences traitées par Haiku en un seul appel :
    (1) Actionnabilité business — tous les piliers à ÉGALITÉ (ROI/cas d'usage, pédagogie/comment-faire,
        conformité, stratégie, vision). La conformité/CNIL n'est PAS prioritaire par défaut.
    (2) Diversité — un article qui reprend un sujet ou un angle déjà publié (recent_topics) est scoré 0-2.
        Haiku juge la similarité SÉMANTIQUEMENT : un dédup par mots-clés laissait passer
        « sanction CNIL IQVIA » vs « sanction CNIL Doctolib » (même angle, mots-clés différents).
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
    if recent_topics:
        recent_block = (
            "<deja_publie_recemment>\n"
            "Sujets/angles DÉJÀ couverts ces dernières semaines. Tout article reprenant le même sujet,\n"
            "la même affaire OU le même angle qu'un de ces posts = score 0-2 (on ne se répète pas) :\n"
            + "\n".join(f"- {t}" for t in recent_topics)
            + "\n</deja_publie_recemment>\n\n"
        )
    else:
        recent_block = ""
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
                    "MAJORITÉ NON TECHNIQUE. Vocabulaire BUSINESS. Sensibles au passage à l'acte : "
                    "ROI, time-to-value, comment s'y prendre — et aussi aux risques "
                    "(coût, lock-in, dépendance fournisseur, conformité).\n"
                    "</audience>\n\n"
                    "<task>\n"
                    "Score chaque article selon sa capacité à devenir un post LinkedIn business "
                    "ACTIONNABLE pour cette audience, en VARIANT les angles d'un post à l'autre. "
                    "Sois STRICT — préfère 5 articles bien scorés à 20 mal scorés.\n"
                    "</task>"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        user_text=(
            recent_block + "<scoring_rubric>\n\n"
            '<tier score="8-10" label="TRÈS PERTINENT">\n'
            "Article dont un dirigeant PME/CTO tire une décision ou une idée concrète après lecture.\n"
            "Les piliers ci-dessous sont À ÉGALITÉ — aucun n'est prioritaire par défaut :\n"
            "- ROI / cas d'usage IA concret en entreprise AVEC chiffres ou bénéfices nommés\n"
            "- Pédagogie / comment-faire : intégrer un LLM, un agent, du RAG, un workflow IA — actionnable\n"
            "- Adoption IA en PME française ou européenne, retours terrain\n"
            "- Stratégie / vision IA avec parti pris exploitable (organisation, build vs buy, gouvernance)\n"
            "- Conformité / régulation IA (CNIL, EU AI Act, RGPD) SI impact direct et décision claire\n"
            "- Annonce produit IA (OpenAI, Anthropic, Mistral) UNIQUEMENT si implications business directes PME\n"
            "</tier>\n\n"
            '<tier score="5-7" label="PERTINENT">\n'
            "- Annonces produit IA généralistes (besoin de pivot business pour activer)\n"
            "- Comparaisons d'outils IA grand public\n"
            "- Études / rapports IA business (McKinsey, BCG, Gartner) si chiffres exploitables\n"
            "</tier>\n\n"
            '<tier score="0-4" label="PAS PERTINENT">\n'
            "- Tech infrastructure pure (training, GPU clusters, fine-tuning) sans angle business\n"
            "- Recherche académique, benchmarks théoriques, papers sans application directe\n"
            "- Hardware (puces, datacenters)\n"
            "- Levées de fonds isolées sans angle stratégique pour la cible\n"
            "- Robotique, autonomous vehicles, vision biomédicale (hors scope)\n"
            "- Apps grand public B2C sans angle B2B\n"
            "- Sujets non-IA (politique, économie générale)\n"
            "- Sujet ou angle DÉJÀ couvert récemment (cf. <deja_publie_recemment>)\n"
            "</tier>\n\n"
            "</scoring_rubric>\n\n"
            "<critical_trap>\n"
            "Deux pièges symétriques :\n"
            "1. Un article OpenAI/Anthropic n'est PAS automatiquement à 8-10. Test : "
            '"un dirigeant PME non-tech tire-t-il une décision concrète ?" Si non → 5-7 max.\n'
            "2. Un article conformité/CNIL n'est PAS automatiquement à 8-10 non plus. S'il reprend "
            "un angle déjà publié (encore une sanction, encore une amende RGPD) → 0-2.\n"
            "</critical_trap>\n\n"
            "<calibration_examples>\n"
            '<example score="9">\n'
            '  Titre: "Comment une PME de 30 personnes a automatisé son support avec un agent IA (-40% de tickets)"\n'
            "  → Cas d'usage PME + chiffre + reproductible. Décision claire.\n"
            "</example>\n"
            '<example score="9">\n'
            '  Titre: "RAG en entreprise : les 3 erreurs qui font halluciner votre assistant interne"\n'
            "  → Pédagogie actionnable, pile sur le métier de Victor.\n"
            "</example>\n"
            '<example score="8">\n'
            '  Titre: "L\'EU AI Act entre en vigueur : ce que ça change pour les PME"\n'
            "  → Régulation à impact direct ET sujet non encore couvert. Actionnable.\n"
            "</example>\n"
            '<example score="7">\n'
            '  Titre: "How Virgin Atlantic ships faster with Codex"\n'
            "  → Cas business avec ROI nommé mais cible grande entreprise. Pivot PME possible.\n"
            "</example>\n"
            '<example score="6">\n'
            '  Titre: "OpenAI named a Leader by Gartner in coding agents"\n'
            "  → Annonce produit nécessitant un pivot fort. Pas immédiatement actionnable.\n"
            "</example>\n"
            '<example score="2">\n'
            '  Titre: "La CNIL inflige une amende à une nouvelle société de santé"\n'
            "  → Encore une sanction RGPD : angle déjà couvert récemment. On ne se répète pas.\n"
            "</example>\n"
            '<example score="1">\n'
            '  Titre: "Macron annonce une rallonge budgétaire pour la défense"\n'
            "  → Hors scope IA business.\n"
            "</example>\n"
            "</calibration_examples>\n\n"
            "<articles_to_score>\n" + articles_str + "\n"
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


def get_top_news(recent_topics: list[str] | None = None, n: int = 5, min_score: int = 5) -> list[dict]:
    """Top N news pertinentes (score >= min_score), scorées sur titre + résumé RSS.

    Le corps complet n'est PAS fetché ici : seul l'article finalement retenu est extrait
    (trafilatura) côté generate_post, pour économiser latence et tokens.

    Pas de fallback silencieux : si le scoring échoue (API down, schéma cassé), l'exception
    remonte et le pipeline s'arrête bruyamment — on ne publie pas sur des articles non triés.
    """
    items = fetch_recent_items()
    if not items:
        print("[rss] no items fetched from any source", file=sys.stderr)
        return []

    scored = score_relevance(items, recent_topics)
    relevant = [item for score, item in scored if score >= min_score][:n]
    if not relevant:
        print(
            f"[rss] {len(items)} items fetched but none scored >= {min_score} — nothing relevant today",
            file=sys.stderr,
        )
    return relevant


if __name__ == "__main__":
    from config import RECENT_TOPICS_FOR_SCORING
    from history import recent_published_topics

    news = get_top_news(recent_topics=recent_published_topics(RECENT_TOPICS_FOR_SCORING))
    print(json.dumps(news, ensure_ascii=False, indent=2))
    if not news:
        sys.exit(0)
