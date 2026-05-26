"""
LinkedIn analytics fetcher — récupère memberCreatorPostAnalytics par post.

⚠️ NÉCESSITE LE PRODUIT COMMUNITY MANAGEMENT API (entité légale, business email,
   privacy policy, review LinkedIn). Avec le produit "Share on LinkedIn" seul
   (scope w_member_social), ce module retournera systématiquement 401/403.

   Alternative pratique : import_analytics_csv.py (parse export CSV manuel
   depuis l'UI LinkedIn Analytics — gratuit, zéro setup, ToS-safe).

API officielle (Microsoft Learn, version 2026-05) :
GET /rest/memberCreatorPostAnalytics?q=entity&entity=(ugc:urn:li:ugcPost:ID)&queryType=METRIC

Scope OAuth requis : r_member_postAnalytics

Métriques récupérées :
  IMPRESSION, MEMBERS_REACHED, RESHARE, REACTION, COMMENT,
  POST_SAVE, POST_SEND, LINK_CLICKS, FOLLOWER_GAINED_FROM_CONTENT,
  PROFILE_VIEW_FROM_CONTENT

Best-effort accuracy selon LinkedIn — pas pour billing.
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

from config import (
    ANALYTICS_LOOKBACK_DAYS,
    LINKEDIN_API_VERSION,
    REQUESTS_TIMEOUT,
)
from history import posts_to_fetch_analytics, upsert_analytics

load_dotenv()

LI_REST = "https://api.linkedin.com/rest"

METRICS_TO_FETCH = [
    "IMPRESSION",
    "MEMBERS_REACHED",
    "RESHARE",
    "REACTION",
    "COMMENT",
    "POST_SAVE",
    "POST_SEND",
    "LINK_CLICKS",
    "FOLLOWER_GAINED_FROM_CONTENT",
    "PROFILE_VIEW_FROM_CONTENT",
]

MAX_HTTP_RETRIES = 3
HTTP_RETRY_BASE_DELAY = 5


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
    }


def _entity_param(linkedin_post_id: str) -> str:
    """Construit le paramètre entity= encodé pour /rest/memberCreatorPostAnalytics.

    Accepte un URN brut ('urn:li:ugcPost:123') ou juste l'ID.
    Détecte le type d'URN (ugcPost vs share) et formate en conséquence.
    """
    if not linkedin_post_id.startswith("urn:"):
        urn = f"urn:li:ugcPost:{linkedin_post_id}"
    else:
        urn = linkedin_post_id

    if ":ugcPost:" in urn:
        kind = "ugc"
    elif ":share:" in urn:
        kind = "share"
    else:
        raise ValueError(f"Unsupported URN type: {urn}")

    encoded_urn = requests.utils.quote(urn, safe="")
    return f"({kind}:{encoded_urn})"


def _request_with_retry(url: str, headers: dict) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_HTTP_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUESTS_TIMEOUT)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "0")) or HTTP_RETRY_BASE_DELAY * (2**attempt)
                print(f"[analytics] 429, sleep {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = HTTP_RETRY_BASE_DELAY * (2**attempt)
                print(f"[analytics] {resp.status_code}, sleep {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            time.sleep(HTTP_RETRY_BASE_DELAY * (2**attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"GET {url} failed after {MAX_HTTP_RETRIES} retries")


class MissingAnalyticsScopeError(RuntimeError):
    """Le token n'a pas le scope r_member_postAnalytics (Community Management API requise)."""


def fetch_metric(linkedin_post_id: str, metric: str, token: str) -> int | None:
    """Fetch un seul metric pour un post (total lifetime). Return None si pas dispo.

    Raise MissingAnalyticsScopeError si HTTP 401/403 (scope manquant) — l'appelant
    doit arrêter la boucle (inutile de retry sur tous les posts/métriques).
    """
    entity = _entity_param(linkedin_post_id)
    url = (
        f"{LI_REST}/memberCreatorPostAnalytics?q=entity&entity={entity}&queryType={metric}&aggregation=TOTAL"
    )
    resp = _request_with_retry(url, _headers(token))
    if resp.status_code in (401, 403):
        raise MissingAnalyticsScopeError(
            f"HTTP {resp.status_code} on memberCreatorPostAnalytics — scope "
            "r_member_postAnalytics manquant ou révoqué. Ce scope requiert le produit "
            "Community Management API (entité légale + review). Utilise "
            "import_analytics_csv.py pour importer manuellement les analytics."
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        print(
            f"[analytics] {metric} for {linkedin_post_id}: HTTP {resp.status_code} — {resp.text[:200]}",
            file=sys.stderr,
        )
        return None
    elements = resp.json().get("elements", [])
    if not elements:
        return 0
    return int(elements[0].get("count", 0))


def fetch_all_for_post(post_id: int, linkedin_post_id: str, token: str) -> dict[str, int]:
    """Fetch les 10 métriques pour un post, persiste en SQLite, renvoie dict.

    Propage MissingAnalyticsScopeError sans la convertir : la boucle parente doit
    arrêter immédiatement (inutile de retenter tous les posts avec un scope absent).
    """
    out: dict[str, int] = {}
    for metric in METRICS_TO_FETCH:
        try:
            count = fetch_metric(linkedin_post_id, metric, token)
        except MissingAnalyticsScopeError:
            raise
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
            # Métrique unique en échec — continue avec les autres
            print(f"[analytics] {metric} for {linkedin_post_id}: {e}", file=sys.stderr)
            continue
        if count is None:
            continue
        upsert_analytics(post_id, metric, count)
        out[metric] = count
    return out


def fetch_recent(days: int = ANALYTICS_LOOKBACK_DAYS) -> dict:
    """Fetch analytics pour tous les posts publiés des N derniers jours.

    Renvoie un dict avec un champ "scope_missing" booléen si le token n'a pas
    r_member_postAnalytics — laisse l'appelant décider quoi faire (warning, skip).
    """
    token = os.environ.get("LI_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("LI_ACCESS_TOKEN missing — run oauth_setup.py")

    posts = posts_to_fetch_analytics(days)
    if not posts:
        print(f"[analytics] no posts to fetch in last {days} days", file=sys.stderr)
        return {"posts_fetched": 0, "metrics_collected": 0, "scope_missing": False}

    metrics_count = 0
    for post_id, linkedin_post_id in posts:
        print(f"[analytics] fetching post #{post_id} ({linkedin_post_id})…", file=sys.stderr)
        try:
            result = fetch_all_for_post(post_id, linkedin_post_id, token)
        except MissingAnalyticsScopeError as e:
            print(f"[analytics] SKIP — {e}", file=sys.stderr)
            return {
                "posts_fetched": 0,
                "metrics_collected": metrics_count,
                "scope_missing": True,
            }
        metrics_count += len(result)

    return {"posts_fetched": len(posts), "metrics_collected": metrics_count, "scope_missing": False}


if __name__ == "__main__":
    summary = fetch_recent()
    if summary.get("scope_missing"):
        print(
            "scope_missing=True — utilise import_analytics_csv.py pour importer "
            "manuellement l'export CSV depuis l'UI LinkedIn",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"posts={summary['posts_fetched']} metrics={summary['metrics_collected']}")
