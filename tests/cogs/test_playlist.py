from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.playlist import PlaylistCog
from music.engine import MusicEngine
from music.playlist_store import PlaylistEntry


def make_interaction():
    interaction = MagicMock()
    interaction.guild_id = 123
    interaction.channel = MagicMock()
    interaction.user = MagicMock()
    interaction.user.voice = MagicMock()
    interaction.user.voice.channel = MagicMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_queue_entries_keeps_direct_url_playable(tmp_path):
    bot = MagicMock()
    engine = MusicEngine(bot, data_dir=str(tmp_path), search_provider=MagicMock())
    cog = PlaylistCog(bot=bot, engine=engine)
    interaction = make_interaction()

    # Seed the player so the engine hands back this mock instead of building a
    # real GuildPlayer.
    player = MagicMock()
    player.connect = AsyncMock()
    player.add_and_play = AsyncMock()
    engine.players[interaction.guild_id] = player

    ok = await cog._queue_entries(
        [PlaylistEntry(title="Stream", url="https://example.com/audio.mp3", source="url", added_by="Alice")],
        interaction,
    )

    assert ok is True
    track = player.add_and_play.await_args.args[0]
    assert track.stream_url == "https://example.com/audio.mp3"
    assert track.needs_resolution is False


@pytest.mark.asyncio
async def test_queue_entries_resolves_local_playlist_entry(tmp_path):
    bot = MagicMock()
    engine = MusicEngine(bot, data_dir=str(tmp_path), search_provider=MagicMock())
    cog = PlaylistCog(bot=bot, engine=engine)
    interaction = make_interaction()

    player = MagicMock()
    player.connect = AsyncMock()
    player.add_and_play = AsyncMock()
    engine.players[interaction.guild_id] = player

    with patch("cogs.playlist.config.MUSIC_DIR", "/music"), \
         patch("cogs.playlist.resolve_local_track") as resolve_local:
        local_track = MagicMock()
        local_track.title = "song.mp3"
        local_track.url = "/music/song.mp3"
        local_track.stream_url = "/music/song.mp3"
        resolve_local.return_value = local_track

        ok = await cog._queue_entries(
            [PlaylistEntry(title="song.mp3", url="song.mp3", source="local", added_by="Alice")],
            interaction,
        )

    assert ok is True
    track = player.add_and_play.await_args.args[0]
    assert track.title == "song.mp3"
    assert track.stream_url == "/music/song.mp3"
