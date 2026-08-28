# cogs/playlist.py
import asyncio
import inspect
import logging
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from music.engine import MusicEngine
from music.interactions import defer_interaction
from music.downloader import download_attachment
from music.playlist_store import PlaylistEntry, PlaylistStore
from music.player import GuildPlayer
from music.track import Track
from music.sources.resolver import detect_source, SourceType
from music.sources.apple_music import resolve_apple_music_with_title
from music.sources.spotify import resolve_spotify_with_title
from music.sources.local import resolve_local_track

logger = logging.getLogger(__name__)

_DEFAULT = "default"
_AUTO_SYNC_INTERVAL_SECONDS = 24 * 60 * 60
_AUTO_SYNC_STARTUP_DELAY_SECONDS = 60


class PlaylistCog(commands.Cog):
    def __init__(self, bot: commands.Bot, engine: MusicEngine):
        self.bot = bot
        self.engine = engine
        self._auto_sync_task: Optional[asyncio.Task] = None

    # State lives on the engine; these keep the handlers below readable.
    @property
    def players(self) -> dict[int, GuildPlayer]:
        return self.engine.players

    @property
    def search_provider(self):
        return self.engine.search_provider

    @property
    def store(self) -> PlaylistStore:
        return self.engine.playlist_store

    async def cog_load(self) -> None:
        self._auto_sync_task = asyncio.create_task(self._auto_sync_loop())

    async def cog_unload(self) -> None:
        if self._auto_sync_task and not self._auto_sync_task.done():
            self._auto_sync_task.cancel()

    async def _auto_sync_loop(self) -> None:
        try:
            await asyncio.sleep(_AUTO_SYNC_STARTUP_DELAY_SECONDS)
            while True:
                await self.sync_imported_playlists_once()
                await asyncio.sleep(_AUTO_SYNC_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    async def sync_imported_playlists_once(self) -> None:
        for guild in getattr(self.bot, "guilds", []):
            sources = self.store.load_sync_sources(guild.id)
            for name, source in sources.items():
                entries, err = await self._entries_from_sync_source(source.url)
                if not entries:
                    message = err or "No tracks returned."
                    logger.warning(
                        "playlist_auto_sync_failed guild=%s name=%s source=%s error=%s",
                        guild.id,
                        name,
                        source.source,
                        message,
                    )
                    self.store.mark_sync_error(guild.id, name, message)
                    continue
                self.store.save(guild.id, name, entries)
                self.store.mark_sync_success(guild.id, name)
                logger.info(
                    "playlist_auto_sync_done guild=%s name=%s source=%s count=%s",
                    guild.id,
                    name,
                    source.source,
                    len(entries),
                )

    async def _entries_from_sync_source(self, url: str) -> tuple[list[PlaylistEntry], Optional[str]]:
        source_type = detect_source(url)
        if source_type == SourceType.SPOTIFY_PLAYLIST:
            queries, _title, err = await resolve_spotify_with_title(url)
            if not queries:
                return [], err or "Couldn't read that Spotify playlist."
            return [
                PlaylistEntry(title=q, url=q, source="search", added_by="auto-sync")
                for q in queries
            ], None
        if source_type == SourceType.APPLE_MUSIC_PLAYLIST:
            queries, _title, err = await resolve_apple_music_with_title(url)
            if not queries:
                return [], err or "Couldn't read that Apple Music playlist."
            return [
                PlaylistEntry(title=q, url=q, source="search", added_by="auto-sync")
                for q in queries
            ], None
        return [], "Only imported Spotify and Apple Music playlists can auto-sync."

    playlist = app_commands.Group(name="playlist", description="Community playlists")

    async def _resolve_entry(self, query: str, requester: str) -> Optional[PlaylistEntry]:
        return await self.engine.resolve_playlist_entry(query, requester)

    async def _queue_entries(self, entries: list[PlaylistEntry], interaction: discord.Interaction) -> bool:
        guild_id = interaction.guild_id
        voice = getattr(interaction.user, "voice", None)
        voice_channel = voice.channel if voice else None
        if not voice_channel:
            await interaction.followup.send("Join a voice channel first.")
            return False
        # One construction path, so playback started from a playlist gets the
        # same history recording and state events as playback started from
        # /play. This previously forked on whether MusicCog was loaded, and the
        # fallback branch silently skipped the play-history hook.
        player = self.engine.get_or_create_player(guild_id, interaction.channel)
        try:
            await player.connect(voice_channel)
        except discord.Forbidden:
            await interaction.followup.send("I need permission to join that channel.")
            return False
        for entry in entries:
            stream_url = None
            url = entry.url
            title = entry.title
            if entry.source == "local":
                if config.MUSIC_DIR:
                    local_track = resolve_local_track(entry.url, entry.added_by, config.MUSIC_DIR)
                    if local_track:
                        url = local_track.url
                        title = local_track.title
                        stream_url = local_track.stream_url
                elif entry.url.startswith("/"):
                    stream_url = entry.url
            elif entry.source == "url":
                stream_url = entry.url
            track = Track(title=title, url=url, source=entry.source, duration=None, requester=entry.added_by, stream_url=stream_url)
            await player.add_and_play(track)
        return True

    @playlist.command(name="list", description="List all playlists")
    async def playlist_list(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        names = self.store.list_playlists(guild_id)
        if not names:
            await interaction.followup.send("No playlists yet.")
            return
        lines = ["**Playlists**"]
        for name in sorted(names):
            entries = self.store.load(guild_id, name)
            lines.append(f"- **{name}** — {len(entries)} track{'s' if len(entries) != 1 else ''}")
        await interaction.followup.send("\n".join(lines))

    @playlist.command(name="add", description="Add a track to a playlist")
    @app_commands.describe(
        query="Song name, Spotify/Apple Music/YouTube URL (blank = now playing)",
        name="Playlist name (default: 'default')",
    )
    async def playlist_add(self, interaction: discord.Interaction, query: Optional[str] = None, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        requester = interaction.user.display_name
        if query:
            entry = await self._resolve_entry(query, requester)
            if not entry:
                await interaction.followup.send("Couldn't resolve that track.")
                return
        else:
            player = self.players.get(guild_id)
            if not player or not player.current:
                await interaction.followup.send("Nothing is playing. Provide a query or play something first.")
                return
            track = player.current
            url, source = track.url, track.source
            if track.source == "attachment" and config.MUSIC_DIR:
                local_path = await download_attachment(track.stream_url, track.title, config.MUSIC_DIR)
                if local_path:
                    url, source = local_path, "local"
            entry = PlaylistEntry(title=track.title, url=url, source=source, added_by=requester)
        position = self.store.add(guild_id, name, entry)
        await interaction.followup.send(f"Added **{entry.title}** to **{name}** (#{position}).")

    @playlist.command(name="show", description="Show tracks in a playlist")
    @app_commands.describe(name="Playlist name (default: 'default')")
    async def playlist_show(self, interaction: discord.Interaction, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        entries = self.store.load(guild_id, name)
        if not entries:
            await interaction.followup.send(f"**{name}** is empty.")
            return
        lines = [f"**{name}** ({len(entries)} tracks)"]
        for i, e in enumerate(entries, 1):
            lines.append(f"{i}. **{e.title}** — {e.added_by}")
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n...[truncated]"
        await interaction.followup.send(text)

    @playlist.command(name="play", description="Queue a playlist")
    @app_commands.describe(name="Playlist name (default: 'default')")
    async def playlist_play(self, interaction: discord.Interaction, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        entries = self.store.load(guild_id, name)
        if not entries:
            await interaction.followup.send(f"**{name}** is empty.")
            return
        if await self._queue_entries(entries, interaction):
            await interaction.followup.send(f"Queued {len(entries)} tracks from **{name}**.")

    @playlist.command(name="shuffle", description="Queue a playlist in random order")
    @app_commands.describe(name="Playlist name (default: 'default')")
    async def playlist_shuffle(self, interaction: discord.Interaction, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        entries = self.store.load(guild_id, name)
        if not entries:
            await interaction.followup.send(f"**{name}** is empty.")
            return
        shuffled = entries.copy()
        random.shuffle(shuffled)
        if await self._queue_entries(shuffled, interaction):
            await interaction.followup.send(f"Shuffled and queued {len(shuffled)} tracks from **{name}**.")

    @playlist.command(name="remove", description="Remove a track from a playlist by number")
    @app_commands.describe(number="Track number from /playlist show", name="Playlist name (default: 'default')")
    async def playlist_remove(self, interaction: discord.Interaction, number: int, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        removed = self.store.remove(guild_id, name, number)
        if removed is None:
            await interaction.followup.send(f"No track at position {number} in **{name}**.")
            return
        await interaction.followup.send(f"Removed **{removed.title}** from **{name}**.")

    @playlist.command(name="delete", description="Delete an entire playlist")
    @app_commands.describe(name="Playlist name to delete")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        if self.store.delete_playlist(guild_id, name):
            await interaction.followup.send(f"Deleted playlist **{name}**.")
        else:
            await interaction.followup.send(f"No playlist named **{name}**.")

    @playlist.command(name="clear", description="Clear all tracks from a playlist")
    @app_commands.describe(name="Playlist name (default: 'default')")
    async def playlist_clear(self, interaction: discord.Interaction, name: str = _DEFAULT):
        if not await defer_interaction(interaction):
            return
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.followup.send("Only usable in a server.", ephemeral=True)
            return
        count = self.store.clear(guild_id, name)
        await interaction.followup.send(f"Cleared {count} tracks from **{name}**.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.exception("Playlist command error: %s", error, exc_info=error)
        try:
            msg = "Something went wrong."
            is_done = interaction.response.is_done()
            if inspect.isawaitable(is_done):
                is_done = await is_done
            if is_done:
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCog(bot, bot.engine))
