import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from music.track import Track
from music.playlist_store import PlaylistSyncSource

def make_interaction(in_voice=True):
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 12345
    interaction.user = MagicMock()
    interaction.user.display_name = "TestUser"
    interaction.channel = MagicMock()
    if in_voice:
        interaction.user.voice = MagicMock()
        interaction.user.voice.channel = MagicMock()
    else:
        interaction.user.voice = None
    return interaction

def test_cog_imports():
    from cogs.music import MusicCog
    assert MusicCog is not None

@pytest.mark.asyncio
async def test_play_not_in_voice():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction(in_voice=False)
    await cog.play.callback(cog, interaction, "test query")
    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once_with("Join a voice channel first.")

@pytest.mark.asyncio
async def test_play_youtube_url():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction()
    mock_track = Track(title="Test", url="https://youtube.com/watch?v=abc",
                       source="youtube", duration=100, requester="TestUser")
    with patch("music.engine.resolve_youtube_track", AsyncMock(return_value=mock_track)), \
         patch.object(cog, "_get_player") as mock_get_player:
        mock_player = MagicMock()
        mock_player.connect = AsyncMock()
        mock_player.add_many_and_play = AsyncMock()
        mock_get_player.return_value = mock_player
        await cog.play.callback(cog, interaction, "https://youtube.com/watch?v=abc")
    mock_player.add_many_and_play.assert_called_once()
    interaction.followup.send.assert_called_once()

@pytest.mark.asyncio
async def test_play_no_results():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction()
    with patch("music.engine.resolve_youtube_track", AsyncMock(return_value=None)):
        await cog.play.callback(cog, interaction, "https://youtube.com/watch?v=bad")
    interaction.followup.send.assert_called_once_with(
        "Couldn't find a playable track from that link."
    )

@pytest.mark.asyncio
async def test_voice_state_update_auto_leave():
    from cogs.music import MusicCog
    bot = MagicMock()
    cog = MusicCog(bot=bot)
    mock_player = MagicMock()
    mock_player.voice_client = MagicMock()
    mock_channel = MagicMock()
    bot_member = MagicMock()
    bot_member.bot = True
    mock_channel.members = [bot_member]
    mock_player.voice_client.channel = mock_channel
    mock_player.disconnect = AsyncMock()
    cog.players[99] = mock_player
    member = MagicMock()
    member.bot = False
    member.guild.id = 99
    await cog.on_voice_state_update(member, MagicMock(), MagicMock())
    mock_player.stop.assert_called_once()
    mock_player.disconnect.assert_called_once()
    assert 99 not in cog.players


@pytest.mark.asyncio
async def test_gtfo_stops_and_disconnects():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction()
    mock_player = MagicMock()
    mock_player.disconnect = AsyncMock()
    cog.players[interaction.guild.id] = mock_player

    await cog.gtfo.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    mock_player.stop.assert_called_once()
    mock_player.disconnect.assert_called_once()
    assert interaction.guild.id not in cog.players
    interaction.followup.send.assert_called_once_with("Stopped and disconnected.")


@pytest.mark.asyncio
async def test_skip_skips_current_track():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction()
    mock_player = MagicMock()
    mock_player.current = Track(
        title="Now",
        url="https://youtube.com/watch?v=abc",
        source="youtube",
        duration=100,
        requester="TestUser",
    )
    cog.players[interaction.guild.id] = mock_player

    await cog.skip.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    mock_player.skip.assert_called_once()
    interaction.followup.send.assert_called_once_with("Skipped: **Now**")


@pytest.mark.asyncio
async def test_queue_shows_current_and_upcoming():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    interaction = make_interaction()
    current = Track(title="Current", url="u1", source="youtube", duration=100, requester="u")
    upcoming = [Track(title="Next", url="u2", source="youtube", duration=120, requester="u")]
    mock_player = MagicMock()
    mock_player.current = current
    mock_player.get_queue_info.return_value = {"current": current, "upcoming": upcoming}
    cog.players[interaction.guild.id] = mock_player

    await cog.queue.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once_with("**Now playing:** Current\n1. Next")


@pytest.mark.asyncio
async def test_player_state_started_updates_presence():
    from cogs.music import MusicCog

    bot = MagicMock()
    bot.change_presence = AsyncMock()
    cog = MusicCog(bot=bot)
    channel = MagicMock()
    channel.send = AsyncMock()
    player = MagicMock()
    player.text_channel = channel
    track = Track(title="Current Track", url="u1", source="youtube", duration=100, requester="u")

    await cog._on_player_state_change(player, "started", track)

    channel.send.assert_not_awaited()
    bot.change_presence.assert_awaited_once()
    activity = bot.change_presence.await_args.kwargs["activity"]
    assert activity.name == "Playing Current Track"


@pytest.mark.asyncio
async def test_player_state_idle_clears_presence():
    from cogs.music import MusicCog

    bot = MagicMock()
    bot.change_presence = AsyncMock()
    cog = MusicCog(bot=bot)
    player = MagicMock()
    player.text_channel = None

    await cog._on_player_state_change(player, "idle", None)

    bot.change_presence.assert_awaited_once_with(activity=None)


@pytest.mark.asyncio
async def test_playlist_auto_sync_replaces_imported_spotify_playlist(tmp_path, monkeypatch):
    from cogs.playlist import PlaylistCog

    from music.engine import MusicEngine

    bot = MagicMock()
    bot.guilds = [SimpleNamespace(id=42)]
    engine = MusicEngine(bot, data_dir=str(tmp_path), search_provider=MagicMock())
    cog = PlaylistCog(bot=bot, engine=engine)
    engine.playlist_store = MagicMock()
    cog.store.load_sync_sources.return_value = {
        "imported_mix": PlaylistSyncSource(
            url="https://open.spotify.com/playlist/abc",
            source="spotify",
        )
    }

    with patch(
        "cogs.playlist.resolve_spotify_with_title",
        AsyncMock(return_value=(["Artist - Song", "Artist - Song 2"], "Imported Mix", None)),
    ):
        await cog.sync_imported_playlists_once()

    cog.store.save.assert_called_once()
    guild_id, name, entries = cog.store.save.call_args.args
    assert guild_id == 42
    assert name == "imported_mix"
    assert [e.title for e in entries] == ["Artist - Song", "Artist - Song 2"]
    cog.store.mark_sync_success.assert_called_once_with(42, "imported_mix")


@pytest.mark.asyncio
async def test_resolve_track_retries_text_search_timeout_then_success():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    track = Track(title="Recovered", url="u", source="youtube", duration=1, requester="")
    search = AsyncMock(side_effect=[asyncio.TimeoutError(), track])
    cog.search_provider.search = search

    with patch("music.engine.config.SEARCH_RETRIES", 1), patch("music.engine.config.SEARCH_RETRY_DELAY", 0):
        resolved, err = await cog._resolve_track("some query", "Tester")

    assert err is None
    assert resolved is track
    assert resolved.requester == "Tester"
    assert search.await_count == 2


@pytest.mark.asyncio
async def test_resolve_track_returns_timeout_after_retry_exhausted():
    from cogs.music import MusicCog
    cog = MusicCog(bot=MagicMock())
    search = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()])
    cog.search_provider.search = search

    with patch("music.engine.config.SEARCH_RETRIES", 1), patch("music.engine.config.SEARCH_RETRY_DELAY", 0):
        resolved, err = await cog._resolve_track("slow query", "Tester")

    assert resolved is None
    assert err == "Search timed out. Please try again."
    assert search.await_count == 2
