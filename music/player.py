import asyncio
import logging
import random
from collections import deque
from typing import Awaitable, Callable, Optional

import discord

import config
from music.track import Track
from music.sources.youtube import resolve_stream_url as yt_resolve_stream_url

logger = logging.getLogger(__name__)


class GuildPlayer:
    def __init__(
        self,
        bot,
        text_channel,
        idle_timeout: int = 300,
        on_idle_disconnect=None,
        on_track_started=None,
        on_state_change: Optional[
            Callable[["GuildPlayer", str, Optional[Track]], Awaitable[None]]
        ] = None,
    ):
        self.bot = bot
        self.text_channel = text_channel
        self.queue: deque[Track] = deque()
        self.history: deque[Track] = deque(maxlen=50)
        self.current: Optional[Track] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self._lock = asyncio.Lock()
        self._idle_task: Optional[asyncio.Task] = None
        self._idle_timeout = idle_timeout
        self._on_idle_disconnect = on_idle_disconnect
        self._on_track_started = on_track_started
        self._on_state_change = on_state_change
        self._play_started_at: Optional[float] = None
        self._pause_started_at: Optional[float] = None
        self._paused_accum: float = 0.0
        self._seeking: bool = False
        # asyncio only keeps a weak reference to a running task, so a
        # fire-and-forget task can be garbage-collected mid-flight. Hold a
        # strong reference until it finishes.
        self._bg_tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        """Run `coro` in the background, keeping a strong reference to it."""
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _schedule_state_change(self, event: str, track: Optional[Track] = None) -> None:
        if self._on_state_change is None:
            return

        async def _run_callback() -> None:
            try:
                await self._on_state_change(self, event, track)
            except Exception as exc:
                logger.warning("on_state_change hook failed event=%s error=%s", event, exc)

        self._spawn(_run_callback())

    async def _emit_state_change(self, event: str, track: Optional[Track] = None) -> None:
        if self._on_state_change is None:
            return
        try:
            await self._on_state_change(self, event, track)
        except Exception as exc:
            logger.warning("on_state_change hook failed event=%s error=%s", event, exc)

    async def add_and_play(self, track: Track) -> None:
        """Add a track to the queue and start playback if idle. Atomic."""
        async with self._lock:
            if not self.voice_client:
                raise RuntimeError("Not connected to a voice channel. Call connect() first.")
            self.queue.append(track)
            self._cancel_idle_timer()
            if not (self.voice_client.is_playing() or self.voice_client.is_paused()):
                await self._play_next_locked()
            else:
                self._spawn(self._prefetch(track))

    async def add_many_and_play(self, tracks: list[Track]) -> None:
        """Bulk-add tracks to the queue. Starts playback if connected and idle;
        if not connected, the tracks wait in the queue and playback begins on the
        next connect(). Only prefetches one upcoming track so a large playlist
        doesn't fan out into many parallel yt-dlp jobs."""
        if not tracks:
            return
        async with self._lock:
            for track in tracks:
                self.queue.append(track)
            self._cancel_idle_timer()
            vc = self.voice_client
            if not (vc and vc.is_connected()):
                return  # queued; playback begins when we join a voice channel
            if not (vc.is_playing() or vc.is_paused()):
                await self._play_next_locked()
            else:
                next_track = next((t for t in self.queue if t.needs_resolution), None)
                if next_track:
                    self._spawn(self._prefetch(next_track))

    def skip(self) -> None:
        if self.voice_client and self.voice_client.is_playing():
            skipped = self.current
            self.voice_client.stop()  # Triggers the after callback -> _on_track_end
            self._schedule_state_change("skipped", skipped)

    def stop(self) -> None:
        stopped = self.current
        self.queue.clear()
        self.current = None
        self._reset_progress()
        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
        self._schedule_state_change("stopped", stopped)

    def pause(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            self._pause_started_at = asyncio.get_running_loop().time()
            self._schedule_state_change("paused", self.current)
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            if self._pause_started_at is not None:
                self._paused_accum += asyncio.get_running_loop().time() - self._pause_started_at
                self._pause_started_at = None
            self._schedule_state_change("resumed", self.current)
            return True
        return False

    def elapsed_seconds(self) -> Optional[float]:
        """Seconds elapsed since the current track started, excluding paused time."""
        if self._play_started_at is None:
            return None
        now = asyncio.get_running_loop().time()
        elapsed = now - self._play_started_at - self._paused_accum
        if self._pause_started_at is not None:
            elapsed -= now - self._pause_started_at
        return elapsed

    async def remove_from_queue(self, index: int) -> Optional[Track]:
        """Remove the track at `index` in the upcoming queue (0 = next-to-play).
        Returns the removed track, or None if the index is out of range."""
        async with self._lock:
            if index < 0 or index >= len(self.queue):
                return None
            items = list(self.queue)
            removed = items.pop(index)
            self.queue.clear()
            self.queue.extend(items)
            return removed

    async def move_in_queue(self, src: int, dst: int) -> bool:
        """Move a queued track from `src` to `dst` (both 0-based, upcoming queue)."""
        async with self._lock:
            n = len(self.queue)
            if src == dst or src < 0 or dst < 0 or src >= n or dst >= n:
                return False
            items = list(self.queue)
            item = items.pop(src)
            items.insert(dst, item)
            self.queue.clear()
            self.queue.extend(items)
            return True

    async def shuffle_queue(self) -> int:
        """Shuffle the upcoming queue in place. Returns the new queue length."""
        async with self._lock:
            random.shuffle(self.queue)
            return len(self.queue)

    async def seek(self, seconds: float) -> bool:
        """Restart the current track's stream at `seconds` from the start.
        FFmpeg has no real seek for an active source, so we tear down the
        current source and spawn a new one with `-ss <seconds>`.
        Returns False if there's nothing to seek in (not connected, no track,
        or no resolved stream URL)."""
        async with self._lock:
            vc = self.voice_client
            if not (vc and vc.is_connected()):
                return False
            track = self.current
            if not track or not track.stream_url:
                return False
            seconds = max(0.0, float(seconds))
            if track.duration is not None and track.duration > 1:
                # Stay 1s shy of the very end so we don't immediately advance.
                seconds = min(seconds, float(track.duration - 1))

            # Tell _on_track_end (which fires from the after callback when we
            # stop the current playback) to bail out instead of advancing.
            self._seeking = True
            if vc.is_playing() or vc.is_paused():
                vc.stop()

            before_opts = (
                f"-ss {seconds:.3f} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                if track.source not in ("local", "attachment")
                else f"-ss {seconds:.3f}"
            )
            loop = asyncio.get_running_loop()
            try:
                source = await loop.run_in_executor(
                    None,
                    lambda: discord.FFmpegOpusAudio(track.stream_url, before_options=before_opts),
                )
            except Exception as exc:
                logger.error("seek_ffmpeg_failed title=%r error=%s", track.title, exc)
                self._seeking = False
                return False

            def after_callback(err):
                coro = self._on_track_end(error=err)
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

            vc.play(source, after=after_callback)
            # Reset elapsed tracking so elapsed_seconds() reflects the new offset.
            self._play_started_at = loop.time() - seconds
            self._paused_accum = 0.0
            self._pause_started_at = None
            logger.info("seek title=%r to=%.2fs", track.title, seconds)
            self._schedule_state_change("seeked", track)
            return True

    def _reset_progress(self) -> None:
        self._play_started_at = None
        self._pause_started_at = None
        self._paused_accum = 0.0

    def get_queue_info(self) -> dict:
        return {
            "current": self.current,
            "upcoming": list(self.queue),
        }

    async def prev(self) -> bool:
        async with self._lock:
            if not self.history:
                return False
            previous = self.history.pop()
            if self.current is not None:
                self.queue.appendleft(self.current)
            self.queue.appendleft(previous)
            self._cancel_idle_timer()
            if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                self.current = None  # Prevent _on_track_end from adding it to history again
                # voice_client.stop() returns immediately; the after_callback
                # schedules _on_track_end via run_coroutine_threadsafe, which
                # won't acquire self._lock until this method releases it.
                self.voice_client.stop()
            else:
                await self._play_next_locked()
            return True

    async def _prefetch(self, track: Track) -> None:
        """Resolve stream URL in the background while other tracks are playing."""
        if not track.needs_resolution:
            return
        try:
            stream_url, thumbnail = await asyncio.wait_for(
                yt_resolve_stream_url(track),
                timeout=config.STREAM_RESOLVE_TIMEOUT,
            )
            track.stream_url = stream_url
            if thumbnail and not track.thumbnail:
                track.thumbnail = thumbnail
            logger.info("prefetch_done title=%r", track.title)
        except Exception as exc:
            logger.warning("prefetch_failed title=%r error=%s", track.title, exc)

    async def _on_track_end(self, error=None) -> None:
        """Called from the after callback when a track finishes or errors."""
        if self._seeking:
            # seek() stopped the current playback to restart at a new offset.
            # Don't advance the queue — seek() will start the new source itself.
            self._seeking = False
            return
        if error:
            logger.error("Playback error: %s", error)
            if self.text_channel:
                await self.text_channel.send(
                    "Playback interrupted, skipping to next track."
                )
        async with self._lock:
            await self._play_next_locked()

    async def _play_next_locked(self) -> None:
        """Advance to the next track. Must be called while holding self._lock."""
        while self.queue:
            # If the voice client went away (e.g. leave_voice), stop here and
            # keep the queue intact for the next connect().
            if not (self.voice_client and self.voice_client.is_connected()):
                return
            track = self.queue.popleft()
            if self.current is not None:
                self.history.append(self.current)
            self.current = track
            if track.needs_resolution:
                if track.source == "search":
                    track.url = f"ytsearch1:{track.url}"
                    track.source = "youtube"
                t0 = asyncio.get_event_loop().time()
                try:
                    stream_url, thumbnail = await asyncio.wait_for(
                        yt_resolve_stream_url(track),
                        timeout=config.STREAM_RESOLVE_TIMEOUT,
                    )
                    track.stream_url = stream_url
                    if thumbnail and not track.thumbnail:
                        track.thumbnail = thumbnail
                    logger.info("stream_resolve elapsed_ms=%d title=%r", int((asyncio.get_event_loop().time() - t0) * 1000), track.title)
                except asyncio.TimeoutError:
                    logger.error("Timed out resolving stream URL for: %s", track.title)
                    if self.text_channel:
                        await self.text_channel.send(
                            f"Timed out loading '{track.title}', skipping."
                        )
                    continue
                except Exception as exc:
                    logger.error("Failed to resolve stream URL: %s", exc)
                    if self.text_channel:
                        await self.text_channel.send(
                            f"Failed to load '{track.title}', skipping."
                        )
                    continue
            before_opts = (
                "-ss 0 -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                if track.source not in ("local", "attachment") else "-ss 0"
            )
            stream_url = track.stream_url
            loop = asyncio.get_running_loop()
            t1 = loop.time()
            source = await loop.run_in_executor(
                None,
                lambda: discord.FFmpegOpusAudio(stream_url, before_options=before_opts),
            )
            logger.info("ffmpeg_spawn elapsed_ms=%d title=%r", int((loop.time() - t1) * 1000), track.title)
            def after_callback(err):
                coro = self._on_track_end(error=err)
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            self.voice_client.play(source, after=after_callback)
            self._play_started_at = loop.time()
            self._paused_accum = 0.0
            self._pause_started_at = None
            logger.info("playback_started title=%r", track.title)
            await self._emit_state_change("started", track)
            if self._on_track_started is not None:
                try:
                    self._on_track_started(track)
                except Exception as exc:
                    logger.warning("on_track_started hook failed: %s", exc)
            next_track = next((t for t in self.queue if t.needs_resolution), None)
            if next_track:
                self._spawn(self._prefetch(next_track))
            return
        # Queue exhausted
        self.current = None
        self._reset_progress()
        await self._emit_state_change("idle", None)
        self._start_idle_timer()

    async def leave_voice(self) -> None:
        """Disconnect from voice but keep the queue intact. The currently-playing
        track is pushed back to the front of the queue so the next connect()
        resumes from it."""
        async with self._lock:
            if self.current is not None:
                self.queue.appendleft(self.current)
                self.current = None
            vc = self.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            if vc and vc.is_connected():
                await vc.disconnect()
            self.voice_client = None
            self._cancel_idle_timer()
            self._reset_progress()
            await self._emit_state_change("left", None)

    async def connect(self, channel: discord.VoiceChannel) -> None:
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()
        # Begin playback of anything queued while we were disconnected.
        async with self._lock:
            if self.queue and not (
                self.voice_client.is_playing() or self.voice_client.is_paused()
            ):
                self._cancel_idle_timer()
                await self._play_next_locked()

    async def disconnect(self) -> None:
        self._cancel_idle_timer()
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
        self.voice_client = None
        self.current = None
        self.queue.clear()
        self.history.clear()
        self._reset_progress()
        await self._emit_state_change("disconnected", None)

    def _start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        self._idle_task = asyncio.ensure_future(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None

    async def _idle_disconnect(self) -> None:
        try:
            await asyncio.sleep(self._idle_timeout)
            if self.text_channel:
                await self.text_channel.send("Disconnecting due to inactivity.")
            await self.disconnect()
            if self._on_idle_disconnect:
                self._on_idle_disconnect(self)
        except asyncio.CancelledError:
            pass
