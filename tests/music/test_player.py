import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from music.player import GuildPlayer
from music.track import Track


def make_track(title="Test Song", stream_url=None):
    return Track(
        title=title,
        url=f"https://youtube.com/watch?v={title}",
        source="youtube",
        duration=200,
        requester="TestUser",
        stream_url=stream_url,
    )


@pytest.fixture
def player():
    bot = MagicMock()
    text_channel = MagicMock()
    text_channel.send = AsyncMock()
    return GuildPlayer(bot=bot, text_channel=text_channel)


@pytest.fixture
def state_events():
    events = []

    async def on_state_change(player, event, track):
        events.append((player, event, track))

    return events, on_state_change


@pytest.mark.asyncio
async def test_add_and_play_starts_playback(player):
    track = make_track(stream_url="http://stream.url")
    player.voice_client = MagicMock()
    player.voice_client.is_playing.return_value = False
    player.voice_client.is_paused.return_value = False

    with patch("music.player.discord.FFmpegOpusAudio"):
        await player.add_and_play(track)

    assert len(player.queue) == 0  # Track was dequeued and is now current
    assert player.current == track
    player.voice_client.play.assert_called_once()


@pytest.mark.asyncio
async def test_track_start_emits_state_change(state_events):
    events, on_state_change = state_events
    bot = MagicMock()
    text_channel = MagicMock()
    player = GuildPlayer(bot=bot, text_channel=text_channel, on_state_change=on_state_change)
    track = make_track(stream_url="http://stream.url")
    player.voice_client = MagicMock()
    player.voice_client.is_connected.return_value = True
    player.voice_client.is_playing.return_value = False
    player.voice_client.is_paused.return_value = False
    player.queue.append(track)

    with patch("music.player.discord.FFmpegOpusAudio"):
        async with player._lock:
            await player._play_next_locked()

    assert events == [(player, "started", track)]


@pytest.mark.asyncio
async def test_queue_exhausted_emits_idle_state_change(state_events):
    events, on_state_change = state_events
    bot = MagicMock()
    text_channel = MagicMock()
    player = GuildPlayer(bot=bot, text_channel=text_channel, on_state_change=on_state_change)
    player._start_idle_timer = MagicMock()

    async with player._lock:
        await player._play_next_locked()

    assert events == [(player, "idle", None)]
    player._start_idle_timer.assert_called_once()


@pytest.mark.asyncio
async def test_add_and_play_queues_when_playing(player):
    track = make_track(stream_url="http://stream.url")
    player.voice_client = MagicMock()
    player.voice_client.is_playing.return_value = True
    player.voice_client.is_paused.return_value = False

    await player.add_and_play(track)

    assert len(player.queue) == 1  # Track stays in queue
    player.voice_client.play.assert_not_called()


@pytest.mark.asyncio
async def test_add_many_queues_without_connection(player):
    """Tracks can be queued before joining; playback starts on connect()."""
    player.voice_client = None
    await player.add_many_and_play([make_track("A"), make_track("B")])
    assert len(player.queue) == 2
    assert player.current is None


@pytest.mark.asyncio
async def test_leave_voice_keeps_queue(player):
    """leave_voice disconnects without clearing the queue; current goes to front."""
    player.current = make_track("Current")
    player.queue.append(make_track("Next"))
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    vc.disconnect = AsyncMock()
    player.voice_client = vc

    await player.leave_voice()

    assert player.voice_client is None
    assert player.current is None
    assert len(player.queue) == 2
    assert player.queue[0].title == "Current"
    assert player.queue[1].title == "Next"
    vc.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_starts_queued_tracks(player):
    """Joining a voice channel begins playback of tracks queued while disconnected."""
    player.queue.append(make_track("Queued", stream_url="http://stream.url"))
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    channel = MagicMock()
    channel.connect = AsyncMock(return_value=vc)

    with patch("music.player.discord.FFmpegOpusAudio"):
        await player.connect(channel)

    assert player.current is not None
    assert player.current.title == "Queued"
    vc.play.assert_called_once()


def test_skip_clears_current(player):
    player.voice_client = MagicMock()
    player.voice_client.is_playing.return_value = True
    player.current = make_track()
    player.skip()
    player.voice_client.stop.assert_called_once()


def test_stop_clears_everything(player):
    player.voice_client = MagicMock()
    player.voice_client.is_playing.return_value = True
    player.current = make_track()
    player.queue.append(make_track("Song 2"))
    player.stop()
    assert len(player.queue) == 0
    assert player.current is None


def test_queue_display(player):
    player.current = make_track("Now Playing")
    player.queue.append(make_track("Up Next"))
    player.queue.append(make_track("After That"))
    info = player.get_queue_info()
    assert info["current"].title == "Now Playing"
    assert len(info["upcoming"]) == 2


def test_elapsed_seconds_none_before_playback(player):
    """No track has played -> elapsed_seconds is None."""
    assert player.elapsed_seconds() is None


@pytest.mark.asyncio
async def test_elapsed_seconds_after_play(player):
    """Once a track starts via _play_next_locked, elapsed_seconds is small and positive."""
    track = make_track(stream_url="http://stream.url")
    player.queue.append(track)
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    player.voice_client = vc

    with patch("music.player.discord.FFmpegOpusAudio"):
        async with player._lock:
            await player._play_next_locked()

    elapsed = player.elapsed_seconds()
    assert elapsed is not None
    assert 0.0 <= elapsed < 1.0


@pytest.mark.asyncio
async def test_elapsed_seconds_excludes_paused_interval(player):
    """Time spent paused does not count toward elapsed_seconds."""
    track = make_track(stream_url="http://stream.url")
    player.queue.append(track)
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    player.voice_client = vc

    with patch("music.player.discord.FFmpegOpusAudio"):
        async with player._lock:
            await player._play_next_locked()

    await asyncio.sleep(0.05)
    # Flip the mock so pause() takes the playing branch.
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    assert player.pause() is True

    await asyncio.sleep(0.05)
    elapsed_while_paused = player.elapsed_seconds()

    # Flip the mock again so resume() takes the paused branch.
    vc.is_playing.return_value = False
    vc.is_paused.return_value = True
    assert player.resume() is True

    elapsed_after_resume = player.elapsed_seconds()
    # The 0.05s spent paused should not be added to elapsed time.
    assert elapsed_after_resume == pytest.approx(elapsed_while_paused, abs=0.02)


@pytest.mark.asyncio
async def test_shuffle_queue_preserves_tracks(player):
    """shuffle_queue returns count and preserves the multiset of tracks."""
    titles = [f"T{i}" for i in range(10)]
    for title in titles:
        player.queue.append(make_track(title))

    count = await player.shuffle_queue()

    assert count == len(titles)
    assert len(player.queue) == len(titles)
    assert sorted(t.title for t in player.queue) == sorted(titles)


@pytest.mark.asyncio
async def test_remove_from_queue(player):
    for t in ("A", "B", "C"):
        player.queue.append(make_track(t))
    removed = await player.remove_from_queue(1)
    assert removed is not None and removed.title == "B"
    assert [t.title for t in player.queue] == ["A", "C"]
    # out of range
    assert await player.remove_from_queue(10) is None


@pytest.mark.asyncio
async def test_move_in_queue(player):
    for t in ("A", "B", "C", "D"):
        player.queue.append(make_track(t))
    assert await player.move_in_queue(0, 2) is True
    assert [t.title for t in player.queue] == ["B", "C", "A", "D"]
    assert await player.move_in_queue(3, 0) is True
    assert [t.title for t in player.queue] == ["D", "B", "C", "A"]
    # noop / out of range
    assert await player.move_in_queue(1, 1) is False
    assert await player.move_in_queue(0, 9) is False


@pytest.mark.asyncio
async def test_seek_restarts_with_offset(player):
    track = make_track("Song", stream_url="http://stream.url")
    track.duration = 300
    player.current = track
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    player.voice_client = vc

    with patch("music.player.discord.FFmpegOpusAudio") as ffmpeg:
        ok = await player.seek(42.5)

    assert ok is True
    # ffmpeg invoked with -ss 42.500 in before_options
    args, kwargs = ffmpeg.call_args
    assert "-ss 42.500" in kwargs.get("before_options", "")
    # The previous source was stopped, new one started.
    vc.stop.assert_called_once()
    vc.play.assert_called_once()
    # elapsed reflects the new offset (allow small drift).
    assert abs(player.elapsed_seconds() - 42.5) < 0.5


@pytest.mark.asyncio
async def test_seek_caps_to_one_before_end(player):
    track = make_track("Song", stream_url="http://stream.url")
    track.duration = 100
    player.current = track
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = False
    vc.is_paused.return_value = False
    player.voice_client = vc

    with patch("music.player.discord.FFmpegOpusAudio") as ffmpeg:
        await player.seek(9999)

    args, kwargs = ffmpeg.call_args
    # 100 - 1 = 99
    assert "-ss 99.000" in kwargs.get("before_options", "")


@pytest.mark.asyncio
async def test_seek_without_track_returns_false(player):
    player.current = None
    vc = MagicMock()
    vc.is_connected.return_value = True
    player.voice_client = vc
    assert await player.seek(10) is False


@pytest.mark.asyncio
async def test_seek_after_callback_does_not_advance(player):
    """When seek stops the old playback, the after_callback fires and triggers
    _on_track_end. With _seeking set, it must NOT pop from the queue."""
    track = make_track("Song", stream_url="http://stream.url")
    track.duration = 300
    player.current = track
    player.queue.append(make_track("Next", stream_url="http://next"))
    player._seeking = True

    await player._on_track_end()
    # Queue still has Next; current unchanged; seeking flag consumed.
    assert player._seeking is False
    assert player.current is track
    assert len(player.queue) == 1


@pytest.mark.asyncio
async def test_play_next_skips_failed_resolution(player):
    """If stream URL resolution fails, skip to next track."""
    bad_track = make_track("Bad Track")  # needs_resolution=True (no stream_url)
    good_track = make_track("Good Track", stream_url="http://ok.url")
    player.queue.append(bad_track)
    player.queue.append(good_track)
    player.voice_client = MagicMock()

    with patch("music.player.yt_resolve_stream_url", AsyncMock(side_effect=RuntimeError("fail"))), \
         patch("music.player.discord.FFmpegOpusAudio"):
        await player._on_track_end()

    assert player.current == good_track
    player.voice_client.play.assert_called_once()


@pytest.mark.asyncio
async def test_play_next_unpacks_lazy_stream_resolution(player):
    track = make_track("Needs Resolution")
    player.queue.append(track)
    player.voice_client = MagicMock()

    with patch("music.player.yt_resolve_stream_url", AsyncMock(return_value=("http://stream.url", "http://thumb.url"))), \
         patch("music.player.discord.FFmpegOpusAudio") as ffmpeg:
        await player._on_track_end()

    assert track.stream_url == "http://stream.url"
    assert track.thumbnail == "http://thumb.url"
    ffmpeg.assert_called_once_with("http://stream.url", before_options="-ss 0 -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")


@pytest.mark.asyncio
async def test_prefetch_unpacks_lazy_stream_resolution(player):
    track = make_track("Prefetch")

    with patch("music.player.yt_resolve_stream_url", AsyncMock(return_value=("http://stream.url", "http://thumb.url"))):
        await player._prefetch(track)

    assert track.stream_url == "http://stream.url"
    assert track.thumbnail == "http://thumb.url"
