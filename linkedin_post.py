"""
LinkedIn REST API — endpoint /rest/posts (versioned).

Compatible avec le produit "Share on LinkedIn" (scope w_member_social, self-serve).

Formats supportés :
- post_document_carousel : upload PDF + post avec media (format principal 2026)
- post_text_only         : post texte simple (variation après streak de carrousels)
- post_poll              : DEPRECATED — conservé pour usage manuel, mais retiré du
                           format_selector automatique. Raison : polls = reach trap
                           2026 (1.78x reach mais 0.37x engagement → kill l'algo).

Plus :
- post_first_comment     : commente le post avec un message d'engagement
                           NOTE : ne JAMAIS y mettre de lien externe (workaround mort
                           en 2026 — LinkedIn pénalise jusqu'à -80% la visibilité
                           des commentaires contenant un lien).

Robustesse :
- Timeout sur toutes les requêtes
- Retry exponentiel sur 429/5xx
- Validation taille PDF
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

from config import LINKEDIN_API_VERSION, REQUESTS_TIMEOUT

load_dotenv()

LI_REST = "https://api.linkedin.com/rest"
MIN_PDF_BYTES = 10_000
MAX_HTTP_RETRIES = 3
HTTP_RETRY_BASE_DELAY = 5

POLL_DURATION_DEFAULT = "THREE_DAYS"  # alt : ONE_DAY, SEVEN_DAYS, FOURTEEN_DAYS


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
    }


def _get_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} missing from .env")
    return value


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUESTS_TIMEOUT)
    last_exc: Exception | None = None
    for attempt in range(MAX_HTTP_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)  # noqa: S113 — timeout injected via kwargs.setdefault above
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "0")) or HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[linkedin] 429 rate limit, sleep {wait}s ({attempt+1}/{MAX_HTTP_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[linkedin] {resp.status_code}, sleep {wait}s ({attempt+1}/{MAX_HTTP_RETRIES})", file=sys.stderr)
                time.sleep(wait)
                continue
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            wait = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
            print(f"[linkedin] transport {e}, sleep {wait}s ({attempt+1}/{MAX_HTTP_RETRIES})", file=sys.stderr)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{method} {url} failed after {MAX_HTTP_RETRIES} retries")


def _validate_pdf(pdf_path: str) -> None:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    size = os.path.getsize(pdf_path)
    if size < MIN_PDF_BYTES:
        raise RuntimeError(f"PDF too small ({size}B < {MIN_PDF_BYTES}B): likely corrupted — {pdf_path}")


# ──────────────────────────────────────────────────────────────
# Document upload (carousel PDF)
# ──────────────────────────────────────────────────────────────
def _upload_document(pdf_path: str, token: str, person_urn: str) -> str:
    """Initialize upload + PUT PDF + return document URN."""
    _validate_pdf(pdf_path)

    init_resp = _request_with_retry(
        "POST",
        f"{LI_REST}/documents?action=initializeUpload",
        headers=_headers(token),
        json={"initializeUploadRequest": {"owner": person_urn}},
    )
    init_resp.raise_for_status()
    init_data = init_resp.json()["value"]
    upload_url = init_data["uploadUrl"]
    document_urn = init_data["document"]

    with open(pdf_path, "rb") as f:
        up_resp = _request_with_retry(
            "PUT",
            upload_url,
            data=f,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        up_resp.raise_for_status()
    return document_urn


# ──────────────────────────────────────────────────────────────
# Builders : payload commun /rest/posts
# ──────────────────────────────────────────────────────────────
def _build_base_payload(person_urn: str, commentary: str) -> dict:
    return {
        "author": person_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def _create_post(payload: dict, token: str) -> str:
    resp = _request_with_retry("POST", f"{LI_REST}/posts", headers=_headers(token), json=payload)
    resp.raise_for_status()
    post_urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id", "")
    if not post_urn:
        raise RuntimeError(f"LinkedIn post created but no URN returned: {dict(resp.headers)}")
    print(f"[linkedin] posted: {post_urn}", file=sys.stderr)
    return post_urn


# ──────────────────────────────────────────────────────────────
# Public API — 3 formats
# ──────────────────────────────────────────────────────────────
def post_document_carousel(post_text: str, pdf_path: str, dry_run: bool = False) -> str:
    token = _get_required("LI_ACCESS_TOKEN")
    person_urn = _get_required("LI_PERSON_URN")

    if dry_run:
        print(f"[DRY RUN] document carousel: {post_text[:80]}…", file=sys.stderr)
        print(f"[DRY RUN] pdf : {pdf_path}", file=sys.stderr)
        return "dry-run-id"

    _validate_pdf(pdf_path)
    print("[linkedin] uploading PDF…", file=sys.stderr)
    document_urn = _upload_document(pdf_path, token, person_urn)
    time.sleep(2)  # let LinkedIn finalize the asset

    payload = _build_base_payload(person_urn, post_text)
    payload["content"] = {"media": {"id": document_urn}}
    return _create_post(payload, token)


def post_text_only(post_text: str, dry_run: bool = False) -> str:
    token = _get_required("LI_ACCESS_TOKEN")
    person_urn = _get_required("LI_PERSON_URN")

    if dry_run:
        print(f"[DRY RUN] text-only: {post_text[:80]}…", file=sys.stderr)
        return "dry-run-id"

    payload = _build_base_payload(person_urn, post_text)
    return _create_post(payload, token)


def post_poll(
    post_text: str,
    question: str,
    options: list[str],
    duration: str = POLL_DURATION_DEFAULT,
    dry_run: bool = False,
) -> str:
    """duration ∈ {ONE_DAY, THREE_DAYS, SEVEN_DAYS, FOURTEEN_DAYS}.
    LinkedIn accepte 2 à 4 options.
    """
    if not 2 <= len(options) <= 4:
        raise ValueError(f"Poll requires 2-4 options, got {len(options)}")

    token = _get_required("LI_ACCESS_TOKEN")
    person_urn = _get_required("LI_PERSON_URN")

    if dry_run:
        print(f"[DRY RUN] poll: {question} → {options}", file=sys.stderr)
        return "dry-run-id"

    payload = _build_base_payload(person_urn, post_text)
    payload["content"] = {
        "poll": {
            "question": question,
            "options": [{"text": o} for o in options],
            "settings": {"duration": duration},
        }
    }
    return _create_post(payload, token)


# ──────────────────────────────────────────────────────────────
# 1er commentaire (engagement)
# ──────────────────────────────────────────────────────────────
def post_first_comment(parent_post_urn: str, comment_text: str, dry_run: bool = False) -> str:
    """parent_post_urn : URN renvoyé par post_document_carousel/post_text_only/post_poll.

    LinkedIn n'a pas de notion de "première commentaire" en API : on poste juste
    rapidement après le post. C'est l'appelant qui gère le délai.
    """
    token = _get_required("LI_ACCESS_TOKEN")
    person_urn = _get_required("LI_PERSON_URN")

    if dry_run:
        print(f"[DRY RUN] first comment on {parent_post_urn}: {comment_text[:80]}…", file=sys.stderr)
        return "dry-run-comment-id"

    # URL encoding pour le segment d'URN
    encoded_parent = requests.utils.quote(parent_post_urn, safe="")
    url = f"{LI_REST}/socialActions/{encoded_parent}/comments"
    payload = {
        "actor": person_urn,
        "object": parent_post_urn,
        "message": {"text": comment_text},
    }
    resp = _request_with_retry("POST", url, headers=_headers(token), json=payload)
    resp.raise_for_status()
    comment_urn = resp.headers.get("x-restli-id") or resp.json().get("$URN", "")
    if not comment_urn:
        raise RuntimeError(f"Comment created but no URN returned: {dict(resp.headers)} / {resp.text[:200]}")
    print(f"[linkedin] commented: {comment_urn}", file=sys.stderr)
    return comment_urn
