"""Direct tests for MusicEngine.

Engine behaviour used to be reachable only through a Discord cog or an HTTP
handler, which meant interface bugs and engine bugs looked the same. These
exercise the engine on its own.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from music.engine import MusicEngine
from music.track import Track


def make_engine(tmp_path, **kwargs):
    kwargs.setdefault("search_provider", MagicMock())
    return MusicEngine(MagicMock(), data_dir=str(tmp_path), **kwargs)


# --- player registry -------------------------------------------------------

def test_get_or_create_player_reuses_the_same_player(tmp_path):
    engine = make_engine(tmp_path)

    first = engine.get_or_create_player(1, MagicMock())
    second = engine.get_or_create_player(1, MagicMock())

    assert first is second
    assert list(engine.players) == [1]


def test_get_or_create_player_refreshes_the_text_channel(tmp_path):
    engine = make_engine(tmp_path)
    original, latest = MagicMock(), MagicMock()

    player = engine.get_or_create_player(1, original)
    assert player.text_channel is original

    engine.get_or_create_player(1, latest)
    # Errors should surface wherever the user most recently issued a command.
    assert player.text_channel is latest


def test_get_player_returns_none_for_unknown_guild(tmp_path):
    assert make_engine(tmp_path).get_player(999) is None


def test_players_are_isolated_per_guild(tmp_path):
    engine = make_engine(tmp_path)

    a = engine.get_or_create_player(1, MagicMock())
    b = engine.get_or_create_player(2, MagicMock())

    assert a is not b
    assert set(engine.players) == {1, 2}


def test_drop_player_forgets_only_that_guild(tmp_path):
    engine = make_engine(tmp_path)
    a = engine.get_or_create_player(1, MagicMock())
    engine.get_or_create_player(2, MagicMock())

    engine._drop_player(a)

    assert set(engine.players) == {2}


# --- state listeners -------------------------------------------------------

@pytest.mark.asyncio
async def test_state_change_reaches_every_listener(tmp_path):
    engine = make_engine(tmp_path)
    first, second = AsyncMock(), AsyncMock()
    engine.add_state_listener(first)
    engine.add_state_listener(second)

    player = MagicMock()
    track = Track(title="T", url="u", source="youtube", duration=1, requester="alex")
    await engine._dispatch_state_change(player, "started", track)

    first.assert_awaited_once_with(player, "started", track)
    second.assert_awaited_once_with(player, "started", track)


@pytest.mark.asyncio
async def test_a_failing_listener_does_not_starve_the_others(tmp_path):
    """One broken interface must not take playback down with it."""
    engine = make_engine(tmp_path)
    broken = AsyncMock(side_effect=RuntimeError("interface exploded"))
    healthy = AsyncMock()
    engine.add_state_listener(broken)
    engine.add_state_listener(healthy)

    await engine._dispatch_state_change(MagicMock(), "idle", None)

    healthy.assert_awaited_once()


@pytest.mark.asyncio
async def test_created_players_dispatch_state_through_the_engine(tmp_path):
    engine = make_engine(tmp_path)
    listener = AsyncMock()
    engine.add_state_listener(listener)

    player = engine.get_or_create_player(1, MagicMock())
    await player._emit_state_change("started", None)

    listener.assert_awaited_once()


def test_created_players_record_to_play_history(tmp_path):
    """The hook that was silently missing on one of the old construction
    paths, so playlist plays never reached the history."""
    engine = make_engine(tmp_path)
    engine.history_store = MagicMock()

    player = engine.get_or_create_player(7, MagicMock())
    track = Track(title="T", url="u", source="youtube", duration=1, requester="alex")
    player._on_track_started(track)

    engine.history_store.record.assert_called_once_with(7, track)


# --- resolution ------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_track_treats_a_direct_url_as_playable(tmp_path):
    engine = make_engine(tmp_path)

    track, err = await engine.resolve_track("https://example.com/audio.mp3", "alex")

    assert err is None
    assert track.source == "url"
    assert track.stream_url == "https://example.com/audio.mp3"
    assert track.needs_resolution is False


@pytest.mark.asyncio
async def test_resolve_track_searches_a_plain_query(tmp_path):
    found = Track(title="Creep", url="u", source="youtube", duration=238, requester="")
    engine = make_engine(tmp_path)
    engine.search_provider.search = AsyncMock(return_value=found)

    track, err = await engine.resolve_track("radiohead creep", "alex")

    assert err is None
    assert track.title == "Creep"
    assert track.requester == "alex"


@pytest.mark.asyncio
async def test_resolve_track_reports_a_miss(tmp_path):
    engine = make_engine(tmp_path)
    engine.search_provider.search = AsyncMock(return_value=None)

    track, err = await engine.resolve_track("nothing at all", "alex")

    assert track is None
    assert "No results" in err


@pytest.mark.asyncio
async def test_resolve_tracks_expands_a_spotify_playlist_into_searches(tmp_path):
    engine = make_engine(tmp_path)

    with patch(
        "music.engine.resolve_spotify",
        AsyncMock(return_value=(["A - One", "B - Two"], None)),
    ):
        tracks, err = await engine.resolve_tracks(
            "https://open.spotify.com/playlist/abc", "alex"
        )

    assert err is None
    # Metadata-only source: each entry stays unresolved until it is played.
    assert [t.title for t in tracks] == ["A - One", "B - Two"]
    assert all(t.source == "search" and t.needs_resolution for t in tracks)


# --- playlist entries ------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_playlist_entry_keeps_a_direct_url(tmp_path):
    engine = make_engine(tmp_path)

    entry = await engine.resolve_playlist_entry("https://example.com/a.mp3", "alex")

    assert (entry.source, entry.url) == ("url", "https://example.com/a.mp3")


@pytest.mark.asyncio
async def test_resolve_playlist_entry_searches_a_plain_query(tmp_path):
    engine = make_engine(tmp_path)
    engine.search_provider.search = AsyncMock(
        return_value=Track(title="Creep", url="u", source="youtube", duration=1, requester="")
    )

    entry = await engine.resolve_playlist_entry("radiohead creep", "alex")

    assert (entry.title, entry.source, entry.added_by) == ("Creep", "youtube", "alex")


@pytest.mark.asyncio
async def test_resolve_playlist_entry_rejects_apple_music(tmp_path):
    """Apple Music links carry no resolvable id for a single entry."""
    engine = make_engine(tmp_path)

    entry = await engine.resolve_playlist_entry(
        "https://music.apple.com/us/album/x/123", "alex"
    )

    assert entry is None


# --- lifecycle -------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_disconnects_every_player_and_closes_the_provider(tmp_path):
    engine = make_engine(tmp_path)
    player = MagicMock()
    player.disconnect = AsyncMock()
    engine.players[1] = player
    engine.search_provider.close = AsyncMock()

    await engine.shutdown()

    player.stop.assert_called_once()
    player.disconnect.assert_awaited_once()
    assert engine.players == {}
    engine.search_provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_tolerates_a_provider_without_close(tmp_path):
    engine = make_engine(tmp_path, search_provider=object())

    await engine.shutdown()  # must not raise

    assert engine.players == {}


def test_constructing_the_engine_does_not_require_a_writable_data_dir():
    """The engine is built in MusicBot.__init__, long before anything needs
    to write, so an unwritable DATA_DIR must not break startup."""
    MusicEngine(MagicMock(), data_dir="/nonexistent/unwritable", search_provider=MagicMock())
