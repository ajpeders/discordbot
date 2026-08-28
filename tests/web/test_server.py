from unittest.mock import AsyncMock, MagicMock

from music.search.base import SearchResult
from music.track import Track

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

import web.server as server_mod
from web.server import create_app
from music.playlist_store import PlaylistEntry


def make_bot():
    bot = MagicMock()
    bot.user = "Music#1"

    guild = MagicMock()
    guild.id = 42
    guild.name = "Test Guild"
    guild.voice_channels = []
    bot.guilds = [guild]
    bot.get_guild = lambda gid: guild if gid == 42 else None

    # The API talks to the engine, so that is what the fake bot exposes.
    engine = MagicMock()
    engine.players = {}
    engine.get_player = lambda gid: engine.players.get(gid)
    engine.get_or_create_player = lambda gid, channel: engine.players.setdefault(
        gid, MagicMock()
    )
    engine.history_store = MagicMock()
    engine.history_store.recent.return_value = []
    engine.search_provider = MagicMock()
    engine.search_provider.search_many = AsyncMock(
        return_value=[
            SearchResult(
                title="Test Song",
                url="https://www.youtube.com/watch?v=abc",
                source="youtube",
                duration=200,
                uploader="Test Channel",
                thumbnail="https://i.ytimg.com/vi/abc/default.jpg",
            )
        ]
    )
    store = MagicMock()
    store.list_playlists.return_value = ["chill"]
    store.load.return_value = [
        PlaylistEntry(title="Song A", url="ytsearch:Song A", source="search", added_by="alex"),
    ]
    engine.playlist_store = store
    bot.engine = engine

    games = MagicMock()
    games.enabled = True
    games.starting = False
    games.players_configured = True
    games.is_running = AsyncMock(return_value=(True, None))
    games.players = AsyncMock(return_value=([{"name": "alex", "level": 3}], None))
    games.ensure_started = AsyncMock(return_value=("starting", None))
    games.connect_address = AsyncMock(return_value="203.0.113.9:8211")
    bot.games = games

    # No get_cog: the API depends only on the engine and the services.
    return bot, engine, store


@pytest.fixture
async def client():
    bot, _music, _store = make_bot()
    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    yield c
    await c.close()


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status == 200
    assert (await resp.json())["ok"] is True


async def test_status(client):
    resp = await client.get("/api/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["bot"] == "Music#1"
    assert data["guilds"][0]["name"] == "Test Guild"
    assert data["guilds"][0]["connected"] is False


async def test_now_playing_no_player(client):
    resp = await client.get("/api/guilds/42/now-playing")
    assert resp.status == 200
    data = await resp.json()
    assert data["connected"] is False
    assert data["current"] is None


async def test_unknown_guild_404(client):
    resp = await client.get("/api/guilds/999/now-playing")
    assert resp.status == 404


async def test_list_playlists(client):
    resp = await client.get("/api/guilds/42/playlists")
    assert resp.status == 200
    data = await resp.json()
    assert data["playlists"] == [{"name": "chill", "count": 1}]


async def test_get_playlist(client):
    resp = await client.get("/api/guilds/42/playlists/chill")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"][0]["title"] == "Song A"


async def test_create_playlist(client):
    store = client.server.app[server_mod.BOT_KEY].engine.playlist_store
    store.list_playlists.return_value = ["chill"]

    resp = await client.post("/api/guilds/42/playlists", json={"name": "Road Trip"})

    assert resp.status == 200
    assert await resp.json() == {"name": "road_trip", "count": 0}
    store.save.assert_called_once_with(42, "Road Trip", [])


async def test_create_playlist_conflict(client):
    store = client.server.app[server_mod.BOT_KEY].engine.playlist_store
    store.list_playlists.return_value = ["road_trip"]

    resp = await client.post("/api/guilds/42/playlists", json={"name": "Road Trip"})

    assert resp.status == 409
    store.save.assert_not_called()


async def test_playback_control_no_player(client):
    resp = await client.post("/api/guilds/42/playback/pause")
    assert resp.status == 409


async def test_search_returns_results(client):
    resp = await client.get("/api/search?q=test%20song&limit=3")
    assert resp.status == 200
    data = await resp.json()
    assert data["results"][0]["title"] == "Test Song"
    assert data["results"][0]["url"].endswith("=abc")
    assert data["results"][0]["thumbnail"]


async def test_search_requires_q(client):
    resp = await client.get("/api/search")
    assert resp.status == 400


async def test_history_endpoint(client):
    import web.server as server_mod

    engine = client.server.app[server_mod.BOT_KEY].engine
    engine.history_store.recent = MagicMock(
        return_value=[
            {"ts": 1.0, "title": "Test", "url": "u", "source": "youtube",
             "duration": 100, "requester": "alex", "thumbnail": None},
        ]
    )
    resp = await client.get("/api/guilds/42/history?limit=10&offset=0")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"][0]["title"] == "Test"
    engine.history_store.recent.assert_called_once_with(42, limit=10, offset=0)


async def test_import_playlist_creates_new(monkeypatch):
    bot, _music, store = make_bot()
    store.list_playlists.return_value = ["chill"]
    saved: dict = {}

    def _save(gid, name, entries):
        saved["gid"] = gid
        saved["name"] = name
        saved["entries"] = entries

    store.save.side_effect = _save

    async def fake_resolve(_bot, url, added_by):
        return (
            [PlaylistEntry(title="t1", url="t1", source="search", added_by=added_by)],
            "Awesome Mix",
            None,
        )

    monkeypatch.setattr(server_mod, "_url_to_entries_with_title", fake_resolve)

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post(
            "/api/guilds/42/playlists/import",
            json={"url": "https://example.com/p", "name": "summer"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data == {"name": "summer", "imported": 1}
        assert saved["name"] == "summer"
        assert len(saved["entries"]) == 1
    finally:
        await c.close()


async def test_import_playlist_autoname(monkeypatch):
    bot, _music, store = make_bot()
    store.list_playlists.return_value = []
    saved: dict = {}

    def _save(gid, name, entries):
        saved["name"] = name

    store.save.side_effect = _save

    async def fake_resolve(_bot, _url, added_by):
        return (
            [PlaylistEntry(title="t", url="t", source="search", added_by=added_by)],
            "Awesome Mix",
            None,
        )

    monkeypatch.setattr(server_mod, "_url_to_entries_with_title", fake_resolve)

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post(
            "/api/guilds/42/playlists/import",
            json={"url": "https://example.com/p"},
        )
        assert resp.status == 200
        data = await resp.json()
        # _safe("Awesome Mix") = "awesome_mix"
        assert data["name"] == "awesome_mix"
        assert data["imported"] == 1
        assert saved["name"] == "Awesome Mix"
    finally:
        await c.close()


async def test_import_playlist_autoname_fallback_dedup(monkeypatch):
    bot, _music, store = make_bot()
    # "import" slug is taken — collision should produce "import (2)".
    store.list_playlists.return_value = ["import"]
    saved: dict = {}

    def _save(gid, name, entries):
        saved["name"] = name

    store.save.side_effect = _save

    async def fake_resolve(_bot, _url, added_by):
        # No title derivable from this URL.
        return (
            [PlaylistEntry(title="t", url="t", source="search", added_by=added_by)],
            None,
            None,
        )

    monkeypatch.setattr(server_mod, "_url_to_entries_with_title", fake_resolve)

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post(
            "/api/guilds/42/playlists/import",
            json={"url": "https://example.com/p"},
        )
        assert resp.status == 200
        data = await resp.json()
        # "import (2)" -> _safe -> "import__2_"
        from music.playlist_store import _safe
        assert data["name"] == _safe("import (2)")
        assert saved["name"] == "import (2)"
    finally:
        await c.close()


async def test_import_playlist_conflict(monkeypatch):
    bot, _music, store = make_bot()
    store.list_playlists.return_value = ["chill"]

    async def fake_resolve(_bot, _url, added_by):
        return (
            [PlaylistEntry(title="t", url="t", source="search", added_by=added_by)],
            "x",
            None,
        )

    monkeypatch.setattr(server_mod, "_url_to_entries_with_title", fake_resolve)

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post(
            "/api/guilds/42/playlists/import",
            json={"url": "https://example.com/p", "name": "Chill"},
        )
        assert resp.status == 409
    finally:
        await c.close()


async def test_import_playlist_resolve_fails(monkeypatch):
    bot, _music, store = make_bot()
    store.list_playlists.return_value = []

    async def fake_resolve(_bot, _url, _added_by):
        return [], None, "bad URL"

    monkeypatch.setattr(server_mod, "_url_to_entries_with_title", fake_resolve)

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post(
            "/api/guilds/42/playlists/import",
            json={"url": "https://example.com/p"},
        )
        assert resp.status == 422
        assert (await resp.json())["error"] == "bad URL"
    finally:
        await c.close()


async def test_now_playing_includes_elapsed_and_duration_when_player_set():
    bot, music, _store = make_bot()
    track = Track(
        title="Song",
        url="https://x/y",
        source="youtube",
        duration=210,
        requester="alex",
    )
    player = MagicMock()
    player.current = track
    player.queue = []
    player.voice_client = None
    player.elapsed_seconds = MagicMock(return_value=12.3)
    music.players = {42: player}

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.get("/api/guilds/42/now-playing")
        assert resp.status == 200
        data = await resp.json()
        assert data["elapsed"] == 12.3
        assert data["duration"] == 210
        assert data["current"]["title"] == "Song"
    finally:
        await c.close()


async def test_shuffle_action():
    bot, music, _store = make_bot()
    player = MagicMock()
    player.shuffle_queue = AsyncMock(return_value=7)
    music.players = {42: player}

    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.post("/api/guilds/42/playback/shuffle")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"ok": True, "count": 7}
        player.shuffle_queue.assert_awaited_once()
    finally:
        await c.close()


async def test_list_files(client, tmp_path, monkeypatch):
    (tmp_path / "alpha.mp3").write_bytes(b"a")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "Bravo.FLAC").write_bytes(b"bb")
    (tmp_path / "notes.txt").write_text("ignore me")

    monkeypatch.setattr(server_mod.config, "MUSIC_DIR", str(tmp_path))

    resp = await client.get("/api/files")
    assert resp.status == 200
    data = await resp.json()
    paths = [f["path"] for f in data["files"]]
    # case-insensitive sort; .txt filtered out
    assert paths == ["alpha.mp3", "sub/Bravo.FLAC"]
    sizes = {f["path"]: f["size"] for f in data["files"]}
    assert sizes["alpha.mp3"] == 1
    assert sizes["sub/Bravo.FLAC"] == 2
    assert "truncated" not in data


async def test_list_files_no_music_dir(client, monkeypatch):
    monkeypatch.setattr(server_mod.config, "MUSIC_DIR", None)
    resp = await client.get("/api/files")
    assert resp.status == 200
    assert (await resp.json()) == {"files": []}


async def test_upload_file_saves(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod.config, "MUSIC_DIR", str(tmp_path))

    form = aiohttp.FormData()
    form.add_field(
        "file",
        b"ID3FAKEAUDIO",
        filename="song.mp3",
        content_type="audio/mpeg",
    )
    resp = await client.post("/api/upload", data=form)
    assert resp.status == 200
    data = await resp.json()
    assert data == {"saved": ["song.mp3"]}
    assert (tmp_path / "song.mp3").read_bytes() == b"ID3FAKEAUDIO"

    # Second upload of the same filename should be suffixed " (2)".
    form2 = aiohttp.FormData()
    form2.add_field(
        "file",
        b"more",
        filename="song.mp3",
        content_type="audio/mpeg",
    )
    resp2 = await client.post("/api/upload", data=form2)
    assert resp2.status == 200
    data2 = await resp2.json()
    assert data2 == {"saved": ["song (2).mp3"]}


async def test_upload_rejects_unsupported_extension(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod.config, "MUSIC_DIR", str(tmp_path))

    form = aiohttp.FormData()
    form.add_field(
        "file",
        b"hello",
        filename="notes.txt",
        content_type="text/plain",
    )
    resp = await client.post("/api/upload", data=form)
    assert resp.status == 400


async def test_api_key_enforced(monkeypatch):
    monkeypatch.setattr(server_mod.config, "WEB_API_KEY", "secret")
    bot, _, _ = make_bot()
    app = create_app(bot)
    server = TestServer(app)
    c = TestClient(server)
    await c.start_server()
    try:
        resp = await c.get("/api/status")
        assert resp.status == 401
        resp = await c.get("/api/status", headers={"X-API-Key": "secret"})
        assert resp.status == 200
        resp = await c.get("/api/health")
        assert resp.status == 200  # health is exempt
    finally:
        await c.close()


# --- games -----------------------------------------------------------------

async def test_games_status_reports_running(client):
    resp = await client.get("/api/games/status")
    assert resp.status == 200
    assert await resp.json() == {
        "enabled": True,
        "running": True,
        "starting": False,
        "players_configured": True,
        "error": None,
    }


async def test_games_status_when_control_is_unconfigured(client):
    client.server.app[server_mod.BOT_KEY].games.enabled = False
    resp = await client.get("/api/games/status")
    assert resp.status == 200
    assert await resp.json() == {"enabled": False}


async def test_games_start_returns_immediately_rather_than_waiting_out_the_boot(client):
    resp = await client.post("/api/games/start")
    assert resp.status == 200
    assert (await resp.json())["state"] == "starting"


async def test_games_start_conflicts_while_another_start_is_in_flight(client):
    games = client.server.app[server_mod.BOT_KEY].games
    games.ensure_started = AsyncMock(return_value=("busy", None))
    resp = await client.post("/api/games/start")
    assert resp.status == 409


async def test_games_start_surfaces_a_sidecar_failure(client):
    games = client.server.app[server_mod.BOT_KEY].games
    games.ensure_started = AsyncMock(return_value=("error", "the power service is unreachable"))
    resp = await client.post("/api/games/start")
    assert resp.status == 502
    assert "unreachable" in (await resp.json())["error"]


async def test_games_start_is_rejected_when_unconfigured(client):
    client.server.app[server_mod.BOT_KEY].games.enabled = False
    resp = await client.post("/api/games/start")
    assert resp.status == 503


async def test_games_players_lists_the_roster(client):
    resp = await client.get("/api/games/players")
    assert resp.status == 200
    data = await resp.json()
    assert data["running"] is True
    assert data["players"][0]["name"] == "alex"


async def test_games_players_reports_an_empty_roster_when_the_server_is_off(client):
    games = client.server.app[server_mod.BOT_KEY].games
    games.is_running = AsyncMock(return_value=(False, None))
    resp = await client.get("/api/games/players")
    assert resp.status == 200
    assert await resp.json() == {"running": False, "players": []}


async def test_games_connect_returns_a_literal_address(client):
    resp = await client.get("/api/games/connect")
    assert resp.status == 200
    # Palworld's client rejects hostnames in the Join with IP box.
    assert (await resp.json())["address"] == "203.0.113.9:8211"


async def test_games_endpoints_require_auth(client, monkeypatch):
    monkeypatch.setattr(server_mod.config, "WEB_API_KEY", "secret")
    resp = await client.post("/api/games/start")
    assert resp.status == 401
