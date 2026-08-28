"""Stateless auth for the web API.

Two ways in, both ending at the same signed bearer token:

* **Discord OAuth** (preferred) — proves *who* you are. The token carries the
  Discord user id, display name, avatar, and whether you are a guild admin.
* **Shared password** (`WEB_PASSWORD`) — proves only that you know the secret.
  Kept so a misconfigured OAuth app cannot lock anyone out of their own
  dashboard.

Non-browser clients may still use `X-API-Key` against `WEB_API_KEY`.

Tokens are HMAC-SHA256 signed with `WEB_API_KEY` when set, else the password —
no external dependency and no server-side session store. That also means
rotating `WEB_API_KEY` invalidates every issued token, which is the intended
panic button.

Note this is a signed *bearer* token, not an encrypted one: the claims are
readable by anyone holding it. Only put in it what the holder already knows
about themselves.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from typing import Optional

import config

_DEFAULT_TTL = 30 * 24 * 3600  # 30 days
# The OAuth round trip is a browser redirect or two; anything longer than a few
# minutes is a replay window, not a convenience.
_STATE_TTL = 600

_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
USER_URL = "https://discord.com/api/users/@me"


def auth_enabled() -> bool:
    """Whether any interactive login is configured."""
    return bool(config.WEB_PASSWORD) or oauth_enabled()


def password_enabled() -> bool:
    return bool(config.WEB_PASSWORD)


def oauth_enabled() -> bool:
    return bool(
        config.DISCORD_CLIENT_ID
        and config.DISCORD_CLIENT_SECRET
        and config.DISCORD_REDIRECT_URI
    )


def _secret() -> bytes:
    return (config.WEB_API_KEY or config.WEB_PASSWORD or "").encode()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def _pack(payload: dict) -> str:
    body = _b64e(json.dumps(payload, sort_keys=True).encode())
    return f"{body}.{_sign(body)}"


def _unpack(token: str) -> Optional[dict]:
    """Verify signature and expiry, returning the payload or None."""
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if float(payload.get("exp", 0)) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    return payload


def make_token(ttl_seconds: int = _DEFAULT_TTL, claims: Optional[dict] = None) -> str:
    """Issue a session token. `claims` carries identity when known."""
    payload = dict(claims or {})
    payload["exp"] = int(time.time()) + ttl_seconds
    return _pack(payload)


def verify_token(token: str) -> Optional[dict]:
    """Return the token's claims, or None if it is invalid or expired.

    Truthy on success, so callers may treat it as a boolean.
    """
    return _unpack(token)


def check_password(password: str) -> bool:
    expected = config.WEB_PASSWORD or ""
    return bool(expected) and hmac.compare_digest(password, expected)


# --- Discord OAuth ---------------------------------------------------------

def make_state() -> str:
    """Signed, short-lived CSRF state.

    Signed rather than stored so the callback stays stateless — there is no
    session table to consult, and a state we did not sign cannot be forged
    without the secret.
    """
    return _pack({"n": secrets.token_urlsafe(16), "exp": int(time.time()) + _STATE_TTL})


def verify_state(state: str) -> bool:
    return _unpack(state) is not None


def authorize_url(state: str) -> str:
    """Discord's consent URL.

    Only the `identify` scope is requested. Guild membership is checked with
    the bot's own credentials instead of the `guilds` scope, so logging in does
    not hand us the list of every server the user is in.
    """
    query = urllib.parse.urlencode({
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    })
    return f"{_AUTHORIZE_URL}?{query}"


def avatar_url(user_id: str, avatar_hash: Optional[str]) -> Optional[str]:
    if not avatar_hash:
        return None
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=64"
