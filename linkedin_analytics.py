"""
LinkedIn analytics fetcher — récupère memberCreatorPostAnalytics par post.

API officielle (Microsoft Learn, version 2026-04) :
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
                wait = int(resp.headers.get("Retry-After", "0")) or HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[analytics] 429, sleep {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[analytics] {resp.status_code}, sleep {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            time.sleep(HTTP_RETRY_BASE_DELAY * (2 ** attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"GET {url} failed after {MAX_HTTP_RETRIES} retries")


def fetch_metric(linkedin_post_id: str, metric: str, token: str) -> int | None:
    """Fetch un seul metric pour un post (total lifetime). Return None si pas dispo."""
    entity = _entity_param(linkedin_post_id)
    url = (
        f"{LI_REST}/memberCreatorPostAnalytics"
        f"?q=entity&entity={entity}&queryType={metric}&aggregation=TOTAL"
    )
    resp = _request_with_retry(url, _headers(token))
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        print(f"[analytics] {metric} for {linkedin_post_id}: HTTP {resp.status_code} — {resp.text[:200]}", file=sys.stderr)
        return None
    elements = resp.json().get("elements", [])
    if not elements:
        return 0
    return int(elements[0].get("count", 0))


def fetch_all_for_post(post_id: int, linkedin_post_id: str, token: str) -> dict[str, int]:
    """Fetch les 10 métriques pour un post, persiste en SQLite, renvoie dict."""
    out: dict[str, int] = {}
    for metric in METRICS_TO_FETCH:
        try:
            count = fetch_metric(linkedin_post_id, metric, token)
        except Exception as e:
            print(f"[analytics] {metric} for {linkedin_post_id}: {e}", file=sys.stderr)
            continue
        if count is None:
            continue
        upsert_analytics(post_id, metric, count)
        out[metric] = count
    return out


def fetch_recent(days: int = ANALYTICS_LOOKBACK_DAYS) -> dict:
    """Fetch analytics pour tous les posts publiés des N derniers jours."""
    token = os.environ.get("LI_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("LI_ACCESS_TOKEN missing — run oauth_setup.py")

    posts = posts_to_fetch_analytics(days)
    if not posts:
        print(f"[analytics] no posts to fetch in last {days} days", file=sys.stderr)
        return {"posts_fetched": 0, "metrics_collected": 0}

    metrics_count = 0
    for post_id, linkedin_post_id in posts:
        print(f"[analytics] fetching post #{post_id} ({linkedin_post_id})…", file=sys.stderr)
        result = fetch_all_for_post(post_id, linkedin_post_id, token)
        metrics_count += len(result)

    return {"posts_fetched": len(posts), "metrics_collected": metrics_count}


if __name__ == "__main__":
    summary = fetch_recent()
    print(f"posts={summary['posts_fetched']} metrics={summary['metrics_collected']}")
