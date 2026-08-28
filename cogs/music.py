# cogs/music.py
import inspect
import logging
from datetime import timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from music.engine import MusicEngine
from music.player import GuildPlayer
from music.play_history import PlayHistoryStore
from music.track import Track

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    """The Discord interface to the music engine.

    This cog owns no playback state. It translates slash commands into engine
    calls and renders engine events as Discord presence — the engine itself is
    shared with the HTTP API and anything else that drives playback.
    """

    def __init__(self, bot: commands.Bot, engine: Optional[MusicEngine] = None):
        self.bot = bot
        self.engine = engine or MusicEngine(bot)
        # Presence is a Discord concern, so it is a listener rather than
        # something the engine knows how to do.
        self.engine.add_state_listener(self._on_player_state_change)

    # The engine owns this state; these keep the cog's existing call sites and
    # the interaction handlers below reading naturally.
    @property
    def players(self) -> dict[int, GuildPlayer]:
        return self.engine.players

    @property
    def history_store(self) -> PlayHistoryStore:
        return self.engine.history_store

    @property
    def search_provider(self):
        return self.engine.search_provider

    @search_provider.setter
    def search_provider(self, provider) -> None:
        self.engine.search_provider = provider

    async def cog_unload(self) -> None:
        await self.engine.shutdown()

    @staticmethod
    def _command_name(interaction: discord.Interaction) -> str:
        return interaction.command.qualified_name if interaction.command else "unknown"

    @staticmethod
    def _interaction_age_ms(interaction: discord.Interaction) -> Optional[int]:
        if not interaction.created_at:
            return None
        now = discord.utils.utcnow().replace(tzinfo=timezone.utc)
        created = interaction.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return int((now - created).total_seconds() * 1000)

    def _log_start(self, interaction: discord.Interaction) -> None:
        logger.info(
            "cmd_start name=%s guild_id=%s user_id=%s channel_id=%s age_ms=%s",
            self._command_name(interaction),
            interaction.guild_id,
            interaction.user.id if interaction.user else None,
            interaction.channel_id,
            self._interaction_age_ms(interaction),
        )

    def _log_finish(self, interaction: discord.Interaction, status: str) -> None:
        logger.info(
            "cmd_end name=%s status=%s guild_id=%s user_id=%s",
            self._command_name(interaction),
            status,
            interaction.guild_id,
            interaction.user.id if interaction.user else None,
        )

    async def _send_command_message(
        self, interaction: discord.Interaction, content: str, *, ephemeral: bool = False
    ) -> None:
        try:
            is_done = interaction.response.is_done()
            if inspect.isawaitable(is_done):
                is_done = await is_done
            if is_done:
                if ephemeral:
                    await interaction.followup.send(content, ephemeral=True)
                else:
                    await interaction.followup.send(content)
            else:
                if ephemeral:
                    await interaction.response.send_message(content, ephemeral=True)
                else:
                    await interaction.response.send_message(content)
            return
        except discord.HTTPException:
            pass
        if interaction.channel:
            await interaction.channel.send(content)

    async def _send_fallback_ack(self, interaction: discord.Interaction, command_name: str) -> None:
        if interaction.channel:
            await interaction.channel.send(f"`/{command_name}` interaction expired before I could acknowledge it. Please retry.")

    @staticmethod
    def _short_track_title(track: Optional[Track], *, prefix_len: int = 0) -> str:
        if not track:
            return ""
        max_len = max(20, 128 - prefix_len)
        title = track.title.strip() or "Unknown track"
        return title if len(title) <= max_len else title[: max_len - 1] + "..."

    async def _on_player_state_change(
        self, player: GuildPlayer, event: str, track: Optional[Track]
    ) -> None:
        if event == "started" and track:
            await self._set_presence(f"Playing {self._short_track_title(track, prefix_len=8)}")
            return

        if event == "paused" and track:
            await self._set_presence(f"Paused {self._short_track_title(track, prefix_len=7)}")
            return

        if event == "resumed" and track:
            await self._set_presence(f"Playing {self._short_track_title(track, prefix_len=8)}")
            return

        if event == "seeked" and track:
            await self._set_presence(f"Playing {self._short_track_title(track, prefix_len=8)}")
            return

        if event in {"idle", "stopped", "left", "disconnected"}:
            await self._set_presence(None)

    async def _set_presence(self, name: Optional[str]) -> None:
        activity = discord.Game(name=name) if name else None
        changed = self.bot.change_presence(activity=activity)
        if inspect.isawaitable(changed):
            await changed

    def _get_player(self, guild_id: int, text_channel) -> GuildPlayer:
        return self.engine.get_or_create_player(guild_id, text_channel)

    _DEFER_STALE_MS = 2800  # Discord's acknowledgement window is 3 000 ms

    async def _defer_quick(self, interaction: discord.Interaction, command_name: str) -> bool:
        age_ms = self._interaction_age_ms(interaction)
        if age_ms is not None and age_ms > self._DEFER_STALE_MS:
            logger.warning(
                "Dropping stale /%s interaction age_ms=%s (threshold=%s)",
                command_name,
                age_ms,
                self._DEFER_STALE_MS,
            )
            return False
        try:
            await interaction.response.defer(thinking=True)
            logger.info("cmd_defer_ok name=%s age_ms=%s", command_name, age_ms)
            return True
        except discord.InteractionResponded:
            logger.info("cmd_defer_already_responded name=%s age_ms=%s", command_name, age_ms)
            return True
        except discord.HTTPException as exc:
            logger.warning(
                "Failed to defer interaction in /%s age_ms=%s: HTTP %s discord_code=%s %r",
                command_name,
                age_ms,
                exc.status,
                exc.code,
                exc.text,
            )
            return False

    @staticmethod
    def _guild_id(interaction: discord.Interaction) -> Optional[int]:
        return interaction.guild.id if interaction.guild else None

    @staticmethod
    def _voice_channel(interaction: discord.Interaction) -> Optional[discord.abc.Connectable]:
        voice = getattr(interaction.user, "voice", None)
        return voice.channel if voice else None

    # Resolution is engine logic; these remain so the command handlers and
    # existing tests keep calling the cog.
    async def _resolve_track(self, query: str, requester: str) -> tuple[Optional[Track], Optional[str]]:
        return await self.engine.resolve_track(query, requester)

    async def _resolve_tracks(self, query: str, requester: str) -> tuple[list[Track], Optional[str]]:
        return await self.engine.resolve_tracks(query, requester)

    async def _resolve_attachment(self, attachment: discord.Attachment, requester: str) -> tuple[Optional[Track], Optional[str]]:
        """Download a Discord attachment and create a Track from it."""
        ext = attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""
        if ext not in {"mp3", "flac", "wav", "ogg", "aac", "m4a", "opus", "wma", "mp4"}:
            return None, f"Unsupported file format: `{attachment.filename}`"
        if attachment.content_type and not any(
            attachment.content_type.startswith(t) for t in ("audio/", "video/")
        ) and attachment.content_type != "application/octet-stream":
            return None, f"Unsupported file type: `{attachment.content_type}`"
        track = Track(
            title=attachment.filename,
            url=attachment.url,
            source="attachment",
            duration=None,
            requester=requester,
            stream_url=attachment.url,
        )
        return track, None

    @app_commands.command(name="play", description="Play a song by name, URL, or attached audio file")
    @app_commands.describe(
        query="Song name, YouTube URL, or local filename",
        file="An audio file to play",
    )
    async def play(self, interaction: discord.Interaction, query: Optional[str] = None, file: Optional[discord.Attachment] = None):
        self._log_start(interaction)
        age_ms = self._interaction_age_ms(interaction)
        interaction_ok = await self._defer_quick(interaction, "play")
        if not interaction_ok:
            if age_ms is None or age_ms <= self._DEFER_STALE_MS:
                # Interaction was fresh but defer still failed — let the user know.
                await self._send_fallback_ack(interaction, "play")
            self._log_finish(interaction, "failed_defer_expired")
            return

        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._send_command_message(interaction, "This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild" if interaction_ok else "rejected_not_guild_fallback")
            return
        if not query and not file:
            await self._send_command_message(interaction, "Provide a song name, URL, or attach an audio file.")
            self._log_finish(interaction, "rejected_missing_input" if interaction_ok else "rejected_missing_input_fallback")
            return
        voice_channel = self._voice_channel(interaction)
        if not voice_channel:
            await self._send_command_message(interaction, "Join a voice channel first.")
            self._log_finish(interaction, "rejected_not_in_voice" if interaction_ok else "rejected_not_in_voice_fallback")
            return

        requester = interaction.user.display_name
        await interaction.edit_original_response(content="Searching...")
        if file:
            track, err = await self._resolve_attachment(file, requester)
            tracks = [track] if track else []
        else:
            tracks, err = await self._resolve_tracks(query, requester)

        if not tracks:
            await self._send_command_message(interaction, err or f"No results found for '{query}'.")
            self._log_finish(interaction, "not_found" if interaction_ok else "not_found_fallback")
            return

        await interaction.edit_original_response(content=f"Connecting to **{voice_channel.name}**...")
        player = self._get_player(guild_id, interaction.channel)
        try:
            await player.connect(voice_channel)
        except discord.Forbidden:
            await self._send_command_message(interaction, "I need permission to join and speak in that channel.")
            self._log_finish(interaction, "failed_permissions" if interaction_ok else "failed_permissions_fallback")
            return

        if len(tracks) == 1:
            await interaction.edit_original_response(content=f"Loading **{tracks[0].title}**...")
        else:
            await interaction.edit_original_response(content=f"Loading **{len(tracks)} tracks**...")
        try:
            await player.add_many_and_play(tracks)
        except discord.ClientException as exc:
            if "ffmpeg was not found" in str(exc).lower():
                await self._send_command_message(interaction, "Playback failed: ffmpeg is not installed on the host.")
                self._log_finish(interaction, "failed_missing_ffmpeg" if interaction_ok else "failed_missing_ffmpeg_fallback")
                return
            raise

        if len(tracks) == 1:
            await self._send_command_message(interaction, f"Queued: **{tracks[0].title}**")
        else:
            await self._send_command_message(interaction, f"Queued **{len(tracks)} tracks**.")
        self._log_finish(interaction, "ok" if interaction_ok else "ok_fallback")

    @app_commands.command(name="gtfo", description="Stop playback and leave voice")
    async def gtfo(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await self._send_command_message(interaction, "This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        interaction_ok = await self._defer_quick(interaction, "gtfo")
        if not interaction_ok:
            logger.warning("Aborting /gtfo after defer failure due to expired interaction.")
            await self._send_fallback_ack(interaction, "gtfo")
            self._log_finish(interaction, "failed_defer_expired")
            return

        guild_voice_client = interaction.guild.voice_client if interaction.guild else None
        player = self.players.get(guild_id)
        if player:
            player.stop()
            await player.disconnect()
            self.players.pop(guild_id, None)
            await self._send_command_message(interaction, "Stopped and disconnected.")
            self._log_finish(interaction, "ok" if interaction_ok else "ok_fallback")
            return

        if guild_voice_client and guild_voice_client.is_connected():
            await guild_voice_client.disconnect(force=True)
            await self._send_command_message(interaction, "Disconnected.")
            self._log_finish(interaction, "ok_disconnected_stale_voice" if interaction_ok else "ok_stale_voice_fallback")
            return

        await self._send_command_message(interaction, "Nothing is playing.")
        self._log_finish(interaction, "no_player" if interaction_ok else "no_player_fallback")

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        if not await self._defer_quick(interaction, "skip"):
            self._log_finish(interaction, "failed_defer")
            return
        player = self.players.get(guild_id)
        if not player or not player.current:
            await interaction.followup.send("Nothing is playing.")
            self._log_finish(interaction, "no_player")
            return
        title = player.current.title
        player.skip()
        await interaction.followup.send(f"Skipped: **{title}**")
        self._log_finish(interaction, "ok")

    @app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        if not await self._defer_quick(interaction, "pause"):
            self._log_finish(interaction, "failed_defer")
            return
        player = self.players.get(guild_id)
        if player and player.pause():
            await interaction.followup.send("Paused.")
            self._log_finish(interaction, "ok")
        else:
            await interaction.followup.send("Nothing is playing.")
            self._log_finish(interaction, "no_player")

    @app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        if not await self._defer_quick(interaction, "resume"):
            self._log_finish(interaction, "failed_defer")
            return
        player = self.players.get(guild_id)
        if player and player.resume():
            await interaction.followup.send("Resumed.")
            self._log_finish(interaction, "ok")
        else:
            await interaction.followup.send("Nothing is paused.")
            self._log_finish(interaction, "no_player")

    @app_commands.command(name="prev", description="Play the previous track")
    async def prev(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        if not await self._defer_quick(interaction, "prev"):
            self._log_finish(interaction, "failed_defer")
            return
        player = self.players.get(guild_id)
        if not player:
            await interaction.followup.send("Nothing is playing.")
            self._log_finish(interaction, "no_player")
            return
        moved = await player.prev()
        if not moved:
            await interaction.followup.send("No previous track.")
            self._log_finish(interaction, "no_previous")
            return
        await interaction.followup.send("Playing previous track.")
        self._log_finish(interaction, "ok")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        self._log_start(interaction)
        guild_id = self._guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            self._log_finish(interaction, "rejected_not_guild")
            return
        if not await self._defer_quick(interaction, "queue"):
            self._log_finish(interaction, "failed_defer")
            return
        player = self.players.get(guild_id)
        if not player or not player.current:
            await interaction.followup.send("Queue is empty.")
            self._log_finish(interaction, "no_player")
            return
        info = player.get_queue_info()
        lines = [f"**Now playing:** {info['current'].title}"]
        for i, track in enumerate(info["upcoming"], 1):
            lines.append(f"{i}. {track.title}")
        if not info["upcoming"]:
            lines.append("*No more tracks in queue.*")
        await interaction.followup.send("\n".join(lines))
        self._log_finish(interaction, "ok")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        player = self.players.get(member.guild.id)
        if not player or not player.voice_client:
            return
        channel = player.voice_client.channel
        humans = [m for m in channel.members if not m.bot]
        if not humans:
            player.stop()
            await player.disconnect()
            if member.guild.id in self.players:
                del self.players[member.guild.id]

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.exception("Unhandled app command error: %s", error, exc_info=error)
        message = "Something went wrong while handling that command."
        try:
            is_done = interaction.response.is_done()
            if inspect.isawaitable(is_done):
                is_done = await is_done
            if is_done:
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Failed to send app command error message to user.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot, bot.engine))
