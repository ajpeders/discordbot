"""The music engine — all playback state, owned independently of any interface.

Discord and the HTTP API are both *adapters* over this object. Neither owns
the other's state, and neither has to know the other exists.

Before this existed, `MusicCog` owned the player registry and the stores, so
the web API had to reach the engine through `bot.get_cog("MusicCog")` and even
call its private `_get_player`. That made Discord the root of the process and
the dashboard a guest in its house. Anything that wants to drive playback
(a REST call, a scheduled job, a future CLI or matrix/slack bridge) now talks
to the engine directly.

What is deliberately *not* here: anything that renders to a specific
interface. Setting the bot's Discord presence, replying to an interaction, and
formatting an embed all stay in the cogs. Interfaces subscribe to engine
events via `add_state_listener` instead.

The one unavoidable Discord dependency is audio transport: `GuildPlayer` plays
through `discord.VoiceClient`. The split is orchestration (engine) vs.
transport (Discord), not a claim that the engine is Discord-free.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import config
from music.play_history import PlayHistoryStore
from music.player import GuildPlayer
from music.playlist_store import PlaylistEntry, PlaylistStore
from music.search.youtube_data_api import YouTubeDataAPISearchProvider
from music.search.youtube_search import YouTubeSearchProvider
from music.sources.apple_music import resolve_apple_music
from music.sources.local import probe_duration, resolve_local_track
from music.sources.resolver import SourceType, detect_source
from music.sources.spotify import resolve_spotify
from music.sources.youtube import resolve_youtube_playlist, resolve_youtube_track
from music.track import Track

logger = logging.getLogger(__name__)

StateListener = Callable[[GuildPlayer, str, Optional[Track]], Awaitable[None]]


def build_search_provider():
    """Pick a search backend. The Data API is used when a key is configured;
    otherwise yt-dlp scraping, which is slower but needs no credentials."""
    if config.YOUTUBE_API_KEY:
        logger.info("Search provider: YouTube Data API")
        return YouTubeDataAPISearchProvider(config.YOUTUBE_API_KEY)
    logger.info("Search provider: yt-dlp (no YOUTUBE_API_KEY set)")
    return YouTubeSearchProvider()


class MusicEngine:
    def __init__(self, bot, *, data_dir: Optional[str] = None, search_provider=None):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        directory = data_dir if data_dir is not None else config.DATA_DIR
        self.history_store = PlayHistoryStore(directory)
        self.playlist_store = PlaylistStore(directory)
        self.search_provider = search_provider or build_search_provider()
        self._state_listeners: list[StateListener] = []

    # --- interface hooks ---------------------------------------------------

    def add_state_listener(self, listener: StateListener) -> None:
        """Subscribe to playback events ("started", "paused", "idle", ...).

        Each interface registers its own listener, so adding one never
        requires touching the engine or any other interface.
        """
        self._state_listeners.append(listener)

    async def _dispatch_state_change(
        self, player: GuildPlayer, event: str, track: Optional[Track]
    ) -> None:
        for listener in self._state_listeners:
            try:
                await listener(player, event, track)
            except Exception as exc:
                # One misbehaving interface must not stop playback or starve
                # the others.
                logger.warning("state listener failed event=%s error=%s", event, exc)

    # --- player registry ---------------------------------------------------

    def get_player(self, guild_id: int) -> Optional[GuildPlayer]:
        return self.players.get(guild_id)

    def get_or_create_player(self, guild_id: int, text_channel) -> GuildPlayer:
        """Return the guild's player, creating it if needed.

        `text_channel` is where playback errors get reported, and is refreshed
        on every call so messages land wherever the user most recently issued
        a command from.
        """
        player = self.players.get(guild_id)
        if player is None:
            player = GuildPlayer(
                bot=self.bot,
                text_channel=text_channel,
                idle_timeout=config.IDLE_TIMEOUT,
                on_idle_disconnect=self._drop_player,
                on_track_started=lambda t, gid=guild_id: self.history_store.record(gid, t),
                on_state_change=self._dispatch_state_change,
            )
            self.players[guild_id] = player
        else:
            player.text_channel = text_channel
        return player

    def _drop_player(self, player: GuildPlayer) -> None:
        """Forget a player that disconnected itself after going idle."""
        for guild_id, existing in list(self.players.items()):
            if existing is player:
                del self.players[guild_id]
                break

    # --- resolution --------------------------------------------------------

    async def resolve_track(self, query: str, requester: str) -> tuple[Optional[Track], Optional[str]]:
        """Resolve a query into a Track. Returns (track, error_message)."""
        attempts = max(config.SEARCH_RETRIES, 0) + 1
        source_type = detect_source(query)
        if source_type in (SourceType.YOUTUBE, SourceType.SOUNDCLOUD):
            for attempt in range(1, attempts + 1):
                started = time.monotonic()
                try:
                    track = await asyncio.wait_for(resolve_youtube_track(query, requester), timeout=config.SEARCH_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.info(
                        "resolve_ytdlp source=%s attempt=%s/%s elapsed_ms=%s status=%s",
                        source_type.name,
                        attempt,
                        attempts,
                        elapsed_ms,
                        "hit" if track else "miss",
                    )
                    return (track, None) if track else (None, "Couldn't find a playable track from that link.")
                except asyncio.TimeoutError:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "URL resolution timed out source=%s attempt=%s/%s timeout_s=%s elapsed_ms=%s query=%r",
                        source_type.name,
                        attempt,
                        attempts,
                        config.SEARCH_TIMEOUT,
                        elapsed_ms,
                        query,
                    )
                    if attempt < attempts:
                        await asyncio.sleep(config.SEARCH_RETRY_DELAY)
                        continue
                    return None, "Search timed out. Please try again."
        elif source_type == SourceType.DIRECT_URL:
            track = Track(
                title=query,
                url=query,
                source="url",
                duration=None,
                requester=requester,
                stream_url=query,
            )
            return (track, None)
        elif source_type == SourceType.SPOTIFY:
            queries, err = await resolve_spotify(query)
            if not queries:
                return None, err or "Couldn't extract track info from that Spotify link."
            return await self.resolve_track(queries[0], requester)
        elif source_type == SourceType.APPLE_MUSIC:
            queries, err = await resolve_apple_music(query)
            if not queries:
                return None, err or "Couldn't extract track info from that Apple Music link."
            return await self.resolve_track(queries[0], requester)
        elif source_type == SourceType.LOCAL:
            if config.MUSIC_DIR is None:
                return None, "Local file playback not configured (MUSIC_DIR not set)."
            track = resolve_local_track(query, requester, config.MUSIC_DIR)
            if not track:
                return None, f"File not found: `{query}`"
            # Probe duration so the web UI can render a seek/scrub bar; local
            # files carry no duration metadata otherwise.
            track.duration = await probe_duration(track.stream_url or track.url)
            return track, None
        else:
            for attempt in range(1, attempts + 1):
                started = time.monotonic()
                try:
                    track = await asyncio.wait_for(self.search_provider.search(query), timeout=config.SEARCH_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.info(
                        "text_search attempt=%s/%s elapsed_ms=%s query=%r status=%s",
                        attempt,
                        attempts,
                        elapsed_ms,
                        query,
                        "hit" if track else "miss",
                    )
                    if track:
                        track.requester = requester
                        return (track, None)
                    return (None, f"No results found for '{query}'.")
                except asyncio.TimeoutError:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        "Text search timed out attempt=%s/%s timeout_s=%s elapsed_ms=%s query=%r",
                        attempt,
                        attempts,
                        config.SEARCH_TIMEOUT,
                        elapsed_ms,
                        query,
                    )
                    if attempt < attempts:
                        await asyncio.sleep(config.SEARCH_RETRY_DELAY)
                        continue
                    return None, "Search timed out. Please try again."

    async def resolve_tracks(self, query: str, requester: str) -> tuple[list[Track], Optional[str]]:
        """Resolve a query into one or more Tracks (handles playlists). Returns (tracks, error_message)."""
        source_type = detect_source(query)
        if source_type in (SourceType.YOUTUBE_PLAYLIST, SourceType.SOUNDCLOUD_PLAYLIST):
            started = time.monotonic()
            try:
                tracks = await asyncio.wait_for(
                    resolve_youtube_playlist(query, requester),
                    timeout=config.SEARCH_TIMEOUT * 3,
                )
            except asyncio.TimeoutError:
                logger.warning("Playlist resolution timed out source=%s query=%r", source_type.name, query)
                return [], "Playlist load timed out. Please try again."
            logger.info(
                "resolve_ytdlp_playlist source=%s elapsed_ms=%d count=%d",
                source_type.name,
                int((time.monotonic() - started) * 1000),
                len(tracks),
            )
            if not tracks:
                return [], "Couldn't load any tracks from that playlist."
            return tracks, None
        if source_type == SourceType.SPOTIFY_PLAYLIST:
            started = time.monotonic()
            try:
                queries, err = await asyncio.wait_for(resolve_spotify(query), timeout=config.SEARCH_TIMEOUT * 3)
            except asyncio.TimeoutError:
                logger.warning("Spotify playlist resolution timed out query=%r", query)
                return [], "Playlist load timed out. Please try again."
            logger.info(
                "resolve_spotify_playlist elapsed_ms=%d count=%d",
                int((time.monotonic() - started) * 1000),
                len(queries),
            )
            if not queries:
                return [], err or "Couldn't load any tracks from that Spotify playlist."
            tracks = [
                Track(
                    title=q,
                    url=q,
                    source="search",
                    duration=None,
                    requester=requester,
                )
                for q in queries
            ]
            return tracks, None
        if source_type == SourceType.APPLE_MUSIC_PLAYLIST:
            started = time.monotonic()
            try:
                queries, err = await asyncio.wait_for(resolve_apple_music(query), timeout=config.SEARCH_TIMEOUT * 3)
            except asyncio.TimeoutError:
                logger.warning("Apple Music album resolution timed out query=%r", query)
                return [], "Apple Music load timed out. Please try again."
            logger.info(
                "resolve_apple_music_album elapsed_ms=%d count=%d",
                int((time.monotonic() - started) * 1000),
                len(queries),
            )
            if not queries:
                return [], err or "Couldn't load any tracks from that Apple Music album."
            tracks = [
                Track(title=q, url=q, source="search", duration=None, requester=requester)
                for q in queries
            ]
            return tracks, None
        track, err = await self.resolve_track(query, requester)
        return ([track] if track else []), err

    async def resolve_playlist_entry(self, query: str, requester: str) -> Optional[PlaylistEntry]:
        source_type = detect_source(query)

        if source_type == SourceType.SPOTIFY:
            queries, _ = await resolve_spotify(query)
            if not queries:
                return None
            query = queries[0]
            source_type = SourceType.SEARCH
        elif source_type in (SourceType.APPLE_MUSIC, SourceType.APPLE_MUSIC_PLAYLIST):
            return None

        if source_type in (SourceType.YOUTUBE, SourceType.SOUNDCLOUD):
            track = await resolve_youtube_track(query, requester)
            if not track:
                return None
            return PlaylistEntry(title=track.title, url=track.url, source="youtube", added_by=requester)

        if source_type == SourceType.SEARCH:
            track = await self.search_provider.search(query)
            if not track:
                return None
            track.requester = requester
            return PlaylistEntry(title=track.title, url=track.url, source="youtube", added_by=requester)

        if source_type == SourceType.LOCAL:
            return PlaylistEntry(title=query, url=query, source="local", added_by=requester)

        if source_type == SourceType.DIRECT_URL:
            return PlaylistEntry(title=query, url=query, source="url", added_by=requester)

        return None

    # --- lifecycle ---------------------------------------------------------

    async def shutdown(self) -> None:
        for player in list(self.players.values()):
            player.stop()
            await player.disconnect()
        self.players.clear()
        if hasattr(self.search_provider, "close"):
            await self.search_provider.close()
