import os
import sys
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
IDLE_TIMEOUT: int = int(os.environ.get("IDLE_TIMEOUT", "300"))
SEARCH_TIMEOUT: float = float(os.environ.get("SEARCH_TIMEOUT", "12"))
SEARCH_RETRIES: int = int(os.environ.get("SEARCH_RETRIES", "2"))
SEARCH_RETRY_DELAY: float = float(os.environ.get("SEARCH_RETRY_DELAY", "0.35"))
STREAM_RESOLVE_TIMEOUT: float = float(os.environ.get("STREAM_RESOLVE_TIMEOUT", "12"))
MUSIC_DIR: Optional[str] = os.environ.get("MUSIC_DIR")
DATA_DIR: str = os.environ.get("DATA_DIR", "/data")
GUILD_ID: Optional[str] = os.environ.get("GUILD_ID")
YOUTUBE_API_KEY: Optional[str] = os.environ.get("YOUTUBE_API_KEY")
SPOTIFY_CLIENT_ID: Optional[str] = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET: Optional[str] = os.environ.get("SPOTIFY_CLIENT_SECRET")
LLM_API_BASE_URL: str = os.environ.get("LLM_API_BASE_URL", "")
LLM_API_TIMEOUT: float = float(os.environ.get("LLM_API_TIMEOUT", "20"))
LLM_CHAT_MODEL: str = os.environ.get("LLM_CHAT_MODEL", "")
LLM_API_VERIFY_SSL: bool = os.environ.get("LLM_API_VERIFY_SSL", "1").lower() not in ("0", "false", "no")
WEB_PORT: int = int(os.environ.get("WEB_PORT", "8080"))
WEB_API_KEY: Optional[str] = os.environ.get("WEB_API_KEY")
WEB_PASSWORD: Optional[str] = os.environ.get("WEB_PASSWORD")

# --- Discord OAuth (web/auth.py) ---------------------------------------------
# Optional. When client id + secret + redirect are all set, the dashboard offers
# "Continue with Discord", which gives per-user identity instead of the single
# shared WEB_PASSWORD. The password login stays available so setting these up
# wrong cannot lock anyone out; drop WEB_PASSWORD once OAuth is confirmed
# working to make Discord the only way in.
#
# DISCORD_REDIRECT_URI must match a redirect registered on the Discord
# application exactly, e.g. https://bot.example.com/api/auth/discord/callback
DISCORD_CLIENT_ID: str = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET: str = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI: str = os.environ.get("DISCORD_REDIRECT_URI", "")

# --- game-server power control (cogs/games.py) -------------------------------
# Talks to the homelab's dashboard-power sidecar, which is allowlist-gated and
# owns the docker socket. The bot deliberately does NOT get docker.sock itself:
# it executes YouTube and LLM input, so handing it the socket would be handing
# it the host. Unset POWER_URL disables the cog entirely.
POWER_URL: str = os.environ.get("POWER_URL", "")
POWER_TIMEOUT: float = float(os.environ.get("POWER_TIMEOUT", "15"))
# The one container this cog may touch. Single name on purpose — the sidecar's
# own allowlist also covers minecraft, the VMs and portainer, none of which
# should be reachable from a Discord message.
POWER_SERVICE: str = os.environ.get("POWER_SERVICE", "palworld")
# Optional role gate, checked *in addition* to the allowlist: holding the role
# is an alternative to being individually allowed. Never usable in DMs.
POWER_ROLE: str = os.environ.get("POWER_ROLE", "")
# Extra allowlist admins by Discord user ID (comma-separated). The guild owner
# always counts as one, so this is only needed to delegate to someone else —
# without it there is still never a state where nobody can grant access.
POWER_ADMINS: set[str] = {
    p.strip() for p in os.environ.get("POWER_ADMINS", "").split(",") if p.strip()
}

# Palworld's own REST API, for the roster. Reached directly (both containers are
# on `web`) rather than through dashboard-power, which is deliberately a
# start/stop-only surface and should not grow a query passthrough.
PALWORLD_REST_URL: str = os.environ.get("PALWORLD_REST_URL", "")
PALWORLD_ADMIN_PASSWORD: str = os.environ.get("PALWORLD_ADMIN_PASSWORD", "")
# Fallback only. /palworld connect looks up the live public IP at call time,
# because Palworld's "Join with IP" box rejects hostnames — handing out
# mc.example.com was wrong and did not work in the client. This value is
# used only if that lookup fails.
PALWORLD_CONNECT_HOST: str = os.environ.get("PALWORLD_CONNECT_HOST", "")
PALWORLD_CONNECT_PORT: str = os.environ.get("PALWORLD_CONNECT_PORT", "8211")
PALWORLD_SERVER_PASSWORD: str = os.environ.get("PALWORLD_SERVER_PASSWORD", "")
# Shown to console players, who have no address box and must find the server
# by name in the community browser.
PALWORLD_SERVER_NAME: str = os.environ.get("PALWORLD_SERVER_NAME", "")


_REQUIRED = {
    "BOT_TOKEN": BOT_TOKEN,
    "SPOTIFY_CLIENT_ID": SPOTIFY_CLIENT_ID,
    "SPOTIFY_CLIENT_SECRET": SPOTIFY_CLIENT_SECRET,
}
_missing = [name for name, value in _REQUIRED.items() if not value]
if _missing:
    sys.exit(
        "Missing required environment variable(s): "
        + ", ".join(_missing)
        + ". Set them in .env (Spotify creds: register an app at https://developer.spotify.com/dashboard)."
    )
