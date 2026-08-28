# bot.py
import asyncio
import logging
import socket
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

import config
from music.engine import MusicEngine
from services.games import GamesService
from services.llm import LlmService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        # The engine owns all playback state. Discord and the HTTP API are both
        # adapters over it, so it is created here — above either interface —
        # rather than inside one of them.
        self.engine = MusicEngine(self)
        self.games = GamesService()
        self.llm = LlmService()
        self._synced = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._web_runner = None

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.http.connector = aiohttp.TCPConnector(family=socket.AF_INET, keepalive_timeout=300)
        await super().start(token, reconnect=reconnect)

    async def setup_hook(self):
        await self.load_extension("cogs.music")
        await self.load_extension("cogs.llm")
        await self.load_extension("cogs.playlist")
        # No-ops itself when POWER_URL is unset, so this is safe everywhere.
        await self.load_extension("cogs.games")
        self._monitor_task = self.loop.create_task(self._event_loop_monitor())
        from web.server import start_web_server
        self._web_runner = await start_web_server(self)

    async def _event_loop_monitor(self):
        expected = self.loop.time()
        while not self.is_closed():
            await asyncio.sleep(1)
            now = self.loop.time()
            drift = now - expected - 1
            expected = now
            if drift > 1.0:
                logger.warning("Event loop stall detected: %.3fs", drift)

    async def close(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        if self._web_runner is not None:
            await self._web_runner.cleanup()
        await super().close()

    async def on_ready(self):
        if not self._synced:
            if config.GUILD_ID:
                guild = discord.Object(id=int(config.GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                # Clear any leftover global commands
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                logger.info("Slash commands synced to guild %s.", config.GUILD_ID)
            else:
                await self.tree.sync()
                logger.info("Slash commands synced globally.")
            self._synced = True
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)


def main():
    bot = MusicBot()
    bot.run(config.BOT_TOKEN)


if __name__ == "__main__":
    main()
