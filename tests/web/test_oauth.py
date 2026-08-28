"""Discord OAuth login.

The security-relevant behaviour here is *authorization*, not authentication:
completing Discord's consent screen only proves someone owns a Discord account,
which everyone on Discord does. The membership check is what stops a stranger
walking in.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from web import auth
from web.server import create_app

CLIENT_ID = "client-123"
REDIRECT = "https://bot.example/api/auth/discord/callback"


def make_bot(*, member=None, owner_id=None, raises=None):
    bot = MagicMock()
    bot.user = "Music#1"

    guild = MagicMock()
    guild.id = 42
    guild.owner_id = owner_id

    if raises is not None:
        guild.fetch_member = AsyncMock(side_effect=raises)
    else:
        guild.fetch_member = AsyncMock(return_value=member)

    bot.guilds = [guild]
    bot.get_guild = lambda gid: guild if gid == 42 else None
    bot.engine = MagicMock(players={})
    return bot


def make_member(display_name="Alex", administrator=False):
    member = MagicMock()
    member.display_name = display_name
    member.guild_permissions = MagicMock(administrator=administrator)
    return member


@pytest.fixture
def oauth_config(monkeypatch):
    for mod in ("web.auth.config", "web.server.config"):
        monkeypatch.setattr(f"{mod}.DISCORD_CLIENT_ID", CLIENT_ID)
        monkeypatch.setattr(f"{mod}.DISCORD_CLIENT_SECRET", "shh")
        monkeypatch.setattr(f"{mod}.DISCORD_REDIRECT_URI", REDIRECT)
        monkeypatch.setattr(f"{mod}.WEB_API_KEY", "signing-secret")
        monkeypatch.setattr(f"{mod}.WEB_PASSWORD", None)
        monkeypatch.setattr(f"{mod}.GUILD_ID", "42")
    monkeypatch.setattr("web.server.config.POWER_ADMINS", set())


@pytest.fixture(autouse=True)
def no_real_network():
    """Fail loudly instead of calling discord.com.

    Tests that reject a request before the code exchange do not patch the
    session themselves, so without this a regression that skipped the rejection
    would quietly start making live OAuth calls from the test suite — which is
    exactly what happened while mutation-testing the state check.
    """
    def _boom(*a, **kw):
        raise AssertionError("test attempted a real HTTP call to Discord")

    with patch("web.server.aiohttp.ClientSession", _boom):
        yield


@asynccontextmanager
async def make_client(bot):
    server = TestServer(create_app(bot))
    c = TestClient(server)
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


def fake_discord_session(*, profile, token_status=200, profile_status=200):
    """Stand in for aiohttp.ClientSession during the code exchange."""
    class _Resp:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        async def json(self):
            return self._payload

        async def text(self):
            return str(self._payload)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def post(self, url, data=None):
            return _Resp(token_status, {"access_token": "at"})

        def get(self, url, headers=None):
            return _Resp(profile_status, profile)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return lambda *a, **kw: _Session()


# --- discovery -------------------------------------------------------------

async def test_auth_config_advertises_discord(oauth_config):
    async with make_client(make_bot()) as c:
        resp = await c.get("/api/auth/config")
        assert resp.status == 200
        assert await resp.json() == {"password": False, "discord": True}


async def test_auth_config_is_reachable_without_a_token(oauth_config):
    """The login page has to read this before anyone can log in."""
    async with make_client(make_bot()) as c:
        assert (await c.get("/api/auth/config")).status == 200


# --- authorize redirect ----------------------------------------------------

async def test_login_redirects_to_discord_with_a_signed_state(oauth_config):
    async with make_client(make_bot()) as c:
        resp = await c.get("/api/auth/discord/login", allow_redirects=False)
        assert resp.status == 302
        location = resp.headers["Location"]
        assert location.startswith("https://discord.com/api/oauth2/authorize")
        assert f"client_id={CLIENT_ID}" in location
        # Only `identify` — we do not ask for the user's server list.
        assert "scope=identify" in location
        assert "guilds" not in location

        state = location.split("state=")[1].split("&")[0]
        assert auth.verify_state(state)


async def test_login_is_503_when_oauth_is_unconfigured(monkeypatch):
    monkeypatch.setattr("web.auth.config.DISCORD_CLIENT_ID", "")
    monkeypatch.setattr("web.auth.config.WEB_PASSWORD", "pw")
    monkeypatch.setattr("web.server.config.WEB_API_KEY", None)
    async with make_client(make_bot()) as c:
        resp = await c.get("/api/auth/discord/login", allow_redirects=False)
        assert resp.status == 503


# --- callback --------------------------------------------------------------

async def test_callback_issues_a_token_for_a_guild_member(oauth_config):
    bot = make_bot(member=make_member("Alex"))
    session = fake_discord_session(profile={"id": "777", "username": "alex", "avatar": "abc"})

    async with make_client(bot) as c:
        with patch("web.server.aiohttp.ClientSession", session):
            resp = await c.get(
                f"/api/auth/discord/callback?code=x&state={auth.make_state()}",
                allow_redirects=False,
            )

    assert resp.status == 302
    location = resp.headers["Location"]
    # Fragment, so the token stays out of logs and Referer headers.
    assert "#token=" in location

    import urllib.parse
    token = urllib.parse.unquote(location.split("#token=")[1])
    claims = auth.verify_token(token)
    assert claims["sub"] == "777"
    assert claims["name"] == "Alex"
    assert claims["admin"] is False
    assert claims["avatar"].startswith("https://cdn.discordapp.com/avatars/777/")


async def test_callback_rejects_someone_who_is_not_in_the_guild(oauth_config):
    """The whole point of the membership check: anyone on Discord can pass the
    consent screen, so consent alone must not be enough."""
    bot = make_bot(raises=discord.NotFound(MagicMock(status=404), "nope"))
    session = fake_discord_session(profile={"id": "999", "username": "stranger"})

    async with make_client(bot) as c:
        with patch("web.server.aiohttp.ClientSession", session):
            resp = await c.get(
                f"/api/auth/discord/callback?code=x&state={auth.make_state()}",
                allow_redirects=False,
            )

    assert resp.status == 302
    assert resp.headers["Location"] == "/login?error=not_a_member"
    assert "#token=" not in resp.headers["Location"]


async def test_callback_rejects_a_forged_state(oauth_config):
    """Without this, a third party could trigger a login by feeding the user a
    crafted callback URL."""
    bot = make_bot(member=make_member())
    async with make_client(bot) as c:
        resp = await c.get(
            "/api/auth/discord/callback?code=x&state=not-signed-by-us",
            allow_redirects=False,
        )
    assert resp.headers["Location"] == "/login?error=state"


async def test_callback_rejects_an_expired_state(oauth_config):
    bot = make_bot(member=make_member())
    expired = auth._pack({"n": "x", "exp": 1})
    async with make_client(bot) as c:
        resp = await c.get(
            f"/api/auth/discord/callback?code=x&state={expired}",
            allow_redirects=False,
        )
    assert resp.headers["Location"] == "/login?error=state"


async def test_callback_requires_a_code(oauth_config):
    async with make_client(make_bot()) as c:
        resp = await c.get(
            f"/api/auth/discord/callback?state={auth.make_state()}",
            allow_redirects=False,
        )
    assert resp.headers["Location"] == "/login?error=state"


async def test_callback_handles_a_declined_consent_screen(oauth_config):
    async with make_client(make_bot()) as c:
        resp = await c.get(
            "/api/auth/discord/callback?error=access_denied",
            allow_redirects=False,
        )
    assert resp.headers["Location"] == "/login?error=denied"


async def test_callback_handles_a_failed_code_exchange(oauth_config):
    bot = make_bot(member=make_member())
    session = fake_discord_session(profile={}, token_status=400)
    async with make_client(bot) as c:
        with patch("web.server.aiohttp.ClientSession", session):
            resp = await c.get(
                f"/api/auth/discord/callback?code=bad&state={auth.make_state()}",
                allow_redirects=False,
            )
    assert resp.headers["Location"] == "/login?error=exchange"


async def test_guild_owner_is_marked_admin(oauth_config):
    bot = make_bot(member=make_member("Owner"), owner_id=777)
    session = fake_discord_session(profile={"id": "777", "username": "owner"})
    async with make_client(bot) as c:
        with patch("web.server.aiohttp.ClientSession", session):
            resp = await c.get(
                f"/api/auth/discord/callback?code=x&state={auth.make_state()}",
                allow_redirects=False,
            )
    import urllib.parse
    token = urllib.parse.unquote(resp.headers["Location"].split("#token=")[1])
    assert auth.verify_token(token)["admin"] is True


async def test_administrator_permission_is_marked_admin(oauth_config):
    bot = make_bot(member=make_member("Mod", administrator=True))
    session = fake_discord_session(profile={"id": "555", "username": "mod"})
    async with make_client(bot) as c:
        with patch("web.server.aiohttp.ClientSession", session):
            resp = await c.get(
                f"/api/auth/discord/callback?code=x&state={auth.make_state()}",
                allow_redirects=False,
            )
    import urllib.parse
    token = urllib.parse.unquote(resp.headers["Location"].split("#token=")[1])
    assert auth.verify_token(token)["admin"] is True


# --- identity on the session ----------------------------------------------

async def test_me_returns_the_logged_in_user(oauth_config):
    token = auth.make_token(claims={"sub": "777", "name": "Alex", "admin": False})
    async with make_client(make_bot()) as c:
        resp = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200
        assert (await resp.json())["user"]["name"] == "Alex"


async def test_me_requires_a_token(oauth_config):
    async with make_client(make_bot()) as c:
        assert (await c.get("/api/auth/me")).status == 401


async def test_queued_tracks_are_attributed_to_the_discord_user(oauth_config):
    """Identity is the point of all this — plays should say who asked."""
    bot = make_bot(member=make_member())
    bot.engine.resolve_tracks = AsyncMock(return_value=([], "nothing"))
    token = auth.make_token(claims={"sub": "777", "name": "Alex"})

    async with make_client(bot) as c:
        await c.post(
            "/api/guilds/42/queue",
            json={"query": "a song"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert bot.engine.resolve_tracks.await_args.args[1] == "Alex"


async def test_api_key_callers_still_fall_back_to_web(oauth_config, monkeypatch):
    bot = make_bot()
    bot.engine.resolve_tracks = AsyncMock(return_value=([], "nothing"))

    async with make_client(bot) as c:
        await c.post(
            "/api/guilds/42/queue",
            json={"query": "a song"},
            headers={"X-API-Key": "signing-secret"},
        )

    assert bot.engine.resolve_tracks.await_args.args[1] == "web"
