"""
Refresh du LinkedIn access_token via refresh_token.

À cron tous les ~50 jours (access_token expire à 60 jours).
Le refresh_token expire à 365 jours — re-OAuth manuel requis ensuite.
"""

import os
import sys

import requests
from dotenv import load_dotenv, set_key

from config import REQUESTS_TIMEOUT

load_dotenv()
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


def refresh() -> bool:
    refresh_token = os.environ.get("LI_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        print("[token-refresh] no refresh_token — run oauth_setup.py", file=sys.stderr)
        return False

    client_id = os.environ.get("LI_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LI_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print("[token-refresh] LI_CLIENT_ID or LI_CLIENT_SECRET missing", file=sys.stderr)
        return False

    try:
        resp = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=REQUESTS_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"[token-refresh] transport error: {e}", file=sys.stderr)
        return False

    if resp.status_code != 200:
        # Truncate response body to avoid leaking tokens / credentials if echoed back
        body_preview = resp.text[:200] if resp.text else ""
        print(f"[token-refresh] failed {resp.status_code}: {body_preview}", file=sys.stderr)
        return False

    tokens = resp.json()
    set_key(ENV_FILE, "LI_ACCESS_TOKEN", tokens["access_token"])
    if "refresh_token" in tokens:
        set_key(ENV_FILE, "LI_REFRESH_TOKEN", tokens["refresh_token"])
    expires_in = tokens.get("expires_in", 0)
    print(f"[token-refresh] OK — expires in {expires_in}s (~{expires_in // 86400}d)", file=sys.stderr)
    return True


if __name__ == "__main__":
    sys.exit(0 if refresh() else 1)
