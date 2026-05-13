"""
One-time OAuth 2.0 setup pour LinkedIn — génère access_token + refresh_token.

Scopes : OpenID Connect moderne (openid profile email) + w_member_social.
Le scope legacy `r_basicprofile` n'est plus utilisé.

Exécution sur victorserv (headless) via SSH tunnel local sur 8080 :
    ssh -L 8080:localhost:8080 victormoi@victorserv
    cd ~/linkedin-posts && python oauth_setup.py
Puis ouvrir l'URL affichée dans ton navigateur local.
"""

import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv, set_key

from config import REQUESTS_TIMEOUT

load_dotenv()

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
REDIRECT_URI = "http://localhost:8080/callback"

# Scopes :
# - openid profile email : identité (OpenID Connect, remplace r_basicprofile déprécié)
# - w_member_social      : publier posts + commenter
# - r_member_postAnalytics : lire les métriques de ses propres posts
SCOPES = "openid profile email w_member_social r_member_postAnalytics"

_auth_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "error" in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"OAuth error: {params.get('error_description', params['error'])}".encode())
            return
        _auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Auth code received. You can close this tab.")

    def log_message(self, *_args):
        return  # silence


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"{name} missing from .env")
    return v


def build_auth_url(client_id: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "linkedin_oauth_v2",
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=REQUESTS_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_person_urn(access_token: str) -> str:
    """Uses OpenID Connect userinfo endpoint (replaces deprecated /v2/me)."""
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=REQUESTS_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    sub = data.get("sub")
    if not sub:
        raise RuntimeError(f"userinfo missing 'sub': {data}")
    return f"urn:li:person:{sub}"


def main() -> int:
    client_id = _require("LI_CLIENT_ID")
    client_secret = _require("LI_CLIENT_SECRET")

    url = build_auth_url(client_id)
    print(f"\nOpen this URL in your browser:\n{url}\n", file=sys.stderr)
    print("Waiting for callback on localhost:8080…", file=sys.stderr)

    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()

    if not _auth_code:
        print("[oauth] no code received", file=sys.stderr)
        return 1

    tokens = exchange_code(_auth_code, client_id, client_secret)
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")
    person_urn = get_person_urn(access_token)

    set_key(ENV_FILE, "LI_ACCESS_TOKEN", access_token)
    if refresh_token:
        set_key(ENV_FILE, "LI_REFRESH_TOKEN", refresh_token)
    set_key(ENV_FILE, "LI_PERSON_URN", person_urn)

    expires_in = tokens.get("expires_in", 0)
    print(f"\n[oauth] saved tokens to .env", file=sys.stderr)
    print(f"[oauth] person URN  : {person_urn}", file=sys.stderr)
    print(f"[oauth] access TTL  : {expires_in}s (~{expires_in // 86400}d)", file=sys.stderr)
    if not refresh_token:
        print("[oauth] WARNING: no refresh_token received — you'll need to re-run this in 60 days", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
