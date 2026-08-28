# cogs/llm.py
"""The Discord interface to LlmService.

Owns only what Discord imposes: the 1800-character message limit, and
interaction handling. Everything else — the HTTP client, the SSL policy, the
response-shape normalising — lives in the service so other interfaces get it.
"""
import inspect
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from music.interactions import defer_interaction
from services.llm import LlmService

logger = logging.getLogger(__name__)

# Discord rejects messages over 2000 characters; leave room for our own framing.
_MAX_BODY = 1800
# Discord renders long lists badly, and the router can expose many models.
_MAX_MODELS_SHOWN = 40


def _truncate(text: str) -> str:
    if len(text) <= _MAX_BODY:
        return text
    return text[:_MAX_BODY] + "\n...[truncated]"


class LLMCog(commands.Cog):
    def __init__(self, bot: commands.Bot, llm: Optional[LlmService] = None):
        self.bot = bot
        self.llm = llm or LlmService()

    @app_commands.command(name="llm-models", description="List available models from the LLM router")
    async def llm_models(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        models, err = await self.llm.list_models()
        if err:
            await interaction.followup.send(err)
            return
        if not models:
            await interaction.followup.send("No models found.")
            return

        lines = ["**Available models:**"]
        lines.extend(f"- `{m}`" for m in models[:_MAX_MODELS_SHOWN])
        if len(models) > _MAX_MODELS_SHOWN:
            lines.append(f"...and {len(models) - _MAX_MODELS_SHOWN} more")
        await interaction.followup.send("\n".join(lines))

    @app_commands.command(name="llm-chat", description="Chat with an LLM model")
    @app_commands.describe(model="Model ID (uses LLM_CHAT_MODEL if omitted)", message="Message to send")
    async def llm_chat(self, interaction: discord.Interaction, message: str, model: Optional[str] = None):
        if not await defer_interaction(interaction):
            return
        text, resolved, err = await self.llm.chat(message, model)
        if err:
            # A missing model is the caller's mistake, not a router failure, so
            # keep that one out of the channel.
            ephemeral = not resolved
            await interaction.followup.send(err, ephemeral=ephemeral)
            return
        await interaction.followup.send(f"**{resolved}**\n{_truncate(text)}")

    @app_commands.command(name="llm-generate", description="One-shot prompt to an LLM model")
    @app_commands.describe(model="Model ID", prompt="Prompt to send")
    async def llm_generate(self, interaction: discord.Interaction, model: str, prompt: str):
        if not await defer_interaction(interaction):
            return
        text, backend, err = await self.llm.generate(model, prompt)
        if err:
            await interaction.followup.send(err)
            return
        footer = f"\n-# via {backend}" if backend else ""
        await interaction.followup.send(f"**{model}**\n{_truncate(text)}{footer}")

    @app_commands.command(name="llm-health", description="Show LLM router backend status")
    async def llm_health(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        data, err = await self.llm.health()
        if err:
            await interaction.followup.send(err)
            return

        lines = ["**LLM Router Status**"]
        if data["backends"]:
            lines.extend(f"- `{b['name']}` — {b['status']}" for b in data["backends"])
        elif data["raw"]:
            lines.extend(f"- **{k}**: {v}" for k, v in data["raw"].items())
        else:
            await interaction.followup.send("Healthy.")
            return
        await interaction.followup.send("\n".join(lines))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.exception("Unhandled LLM command error: %s", error, exc_info=error)
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
    await bot.add_cog(LLMCog(bot, bot.llm))
