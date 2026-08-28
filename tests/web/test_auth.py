from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from web import auth
from web.server import create_app


def make_bot():
    bot = MagicMock()
    bot.user = "Music#1"
    bot.guilds = []
    bot.get_guild = lambda gid: None
    bot.get_cog = lambda name: MagicMock(players={})
    return bot


def test_token_roundtrip(monkeypatch):
    monkeypatch.setattr(auth.config, "WEB_API_KEY", "signing-secret")
    monkeypatch.setattr(auth.config, "WEB_PASSWORD", "hunter2")
    token = auth.make_token()
    assert auth.verify_token(token)
    assert not auth.verify_token("garbage.token")
    assert not auth.verify_token(token + "x")


def test_token_expiry(monkeypatch):
    monkeypatch.setattr(auth.config, "WEB_API_KEY", "s")
    monkeypatch.setattr(auth.config, "WEB_PASSWORD", "p")
    expired = auth.make_token(ttl_seconds=-10)
    assert not auth.verify_token(expired)


def test_check_password(monkeypatch):
    monkeypatch.setattr(auth.config, "WEB_PASSWORD", "hunter2")
    assert auth.check_password("hunter2")
    assert not auth.check_password("wrong")
    assert not auth.check_password("")


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr("web.server.config.WEB_API_KEY", None)
    monkeypatch.setattr("web.server.config.WEB_PASSWORD", "hunter2")
    monkeypatch.setattr("web.auth.config.WEB_API_KEY", None)
    monkeypatch.setattr("web.auth.config.WEB_PASSWORD", "hunter2")
    app = create_app(make_bot())
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    yield c
    await c.close()


async def test_protected_without_token_401(client):
    resp = await client.get("/api/status")
    assert resp.status == 401


async def test_login_and_access(client):
    resp = await client.post("/api/login", json={"password": "wrong"})
    assert resp.status == 401

    resp = await client.post("/api/login", json={"password": "hunter2"})
    assert resp.status == 200
    token = (await resp.json())["token"]

    resp = await client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 200


async def test_health_and_login_exempt(client):
    assert (await client.get("/api/health")).status == 200
    # /api/login reachable without a token (it's how you get one)
    assert (await client.post("/api/login", json={"password": "x"})).status == 401
