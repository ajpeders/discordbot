# cogs/games.py
"""Start the Palworld server from Discord.

The homelab stops Palworld ~30 min after the last player leaves (it holds
several GB idle and leaks on top of that). Restarting it is deliberately
manual — a raw UDP game port has nothing to intercept, so there is no
wake-on-demand — which until now meant someone had to reach the dashboard on
the LAN or VPN. This lets whoever wants to play start it themselves.

Design notes:

* **No docker.sock.** This bot executes YouTube and LLM input; giving it the
  socket would give it the host. It calls the `dashboard-power` sidecar
  instead, which owns the socket, is allowlist-gated and lives on a private
  network with no route in from anywhere else.
* **Start only, no stop.** The idle timer already stops the server correctly.
  A `/palworld stop` would just be a way for anyone in the guild to kill a
  live session.
* **One container.** `config.POWER_SERVICE` is a single name, not a
  user-supplied argument, so this command cannot reach minecraft, the VMs or
  portainer even though the sidecar's own allowlist includes them.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from music.interactions import defer_interaction
from services.games import GamesService

logger = logging.getLogger(__name__)

# The server answers RCON within a few seconds of the container running, but is
# not joinable until the world finishes loading — and a boot that pulls a game
# update can take minutes. Poll a bounded while, then hand back with a caveat
# rather than blocking the interaction until Discord kills it.
_POLL_INTERVAL_S = 5
_POLL_ATTEMPTS = 18  # ~90s


class GamesCog(commands.Cog):
    """The Discord interface to GamesService.

    Owns permission checks (which need a Discord identity) and message
    formatting. Everything else — the sidecar client, the Palworld queries,
    the allowlist, the in-flight start guard — lives in the service so other
    interfaces get it too.
    """

    def __init__(self, bot: commands.Bot, games: GamesService | None = None):
        self.bot = bot
        self.games = games or GamesService()

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        """Who may edit the allowlist. The guild owner always qualifies, so
        there is no bootstrap hole where nobody can grant the first permission."""
        guild = interaction.guild
        if guild is not None and interaction.user.id == guild.owner_id:
            return True
        if str(interaction.user.id) in config.POWER_ADMINS:
            return True
        perms = getattr(interaction.user, "guild_permissions", None)
        return bool(perms and perms.administrator)

    # ---- guards -----------------------------------------------------------
    async def _refuse(self, interaction: discord.Interaction) -> str | None:
        """Returns a refusal message, or None if the caller may proceed."""
        if not self.games.enabled:
            return "Server control isn't configured on this bot."
        # dashboard-power has no authentication of its own — it trusts whoever
        # can reach it on the network. So the Discord side is the only gate,
        # and a DM has no guild to check membership against.
        if interaction.guild is None:
            return "This only works in a server, not in DMs."
        if self._is_admin(interaction):
            return None
        if self.games.is_allowed(interaction.user.id):
            return None
        # Holding the configured role is an alternative to being listed.
        if config.POWER_ROLE:
            roles = getattr(interaction.user, "roles", [])
            if any(r.name == config.POWER_ROLE for r in roles):
                return None
        # An empty allowlist means admins only. That is what a whitelist is —
        # falling back to "everyone" when the list is empty would make the
        # feature silently do nothing on the day it is switched on.
        return (
            "You're not on the allowlist for server controls. "
            "Ask an admin to run `/palworld allow` for you."
        )

    # ---- commands ---------------------------------------------------------
    group = app_commands.Group(name="palworld", description="Palworld server controls")

    # ---- allowlist management (admins only) --------------------------------
    @group.command(name="allow", description="Let someone use the Palworld controls")
    @app_commands.describe(user="Who to grant access to")
    async def allow(self, interaction: discord.Interaction, user: discord.Member):
        if not await defer_interaction(interaction):
            return
        if interaction.guild is None:
            await interaction.followup.send("Server-only command.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send(
                "Only server admins can change the allowlist.", ephemeral=True
            )
            return
        if not self.games.allow(user.id):
            await interaction.followup.send(f"{user.display_name} already has access.")
            return
        logger.info("allowlist: %s added %s (%s)", interaction.user, user, user.id)
        await interaction.followup.send(f"✅ {user.display_name} can now use `/palworld`.")

    @group.command(name="deny", description="Remove someone's Palworld access")
    @app_commands.describe(user="Who to revoke")
    async def deny(self, interaction: discord.Interaction, user: discord.Member):
        if not await defer_interaction(interaction):
            return
        if interaction.guild is None:
            await interaction.followup.send("Server-only command.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send(
                "Only server admins can change the allowlist.", ephemeral=True
            )
            return
        if not self.games.deny(user.id):
            # Say so plainly rather than implying a revoke happened — they may
            # still have access via admin rights or the role.
            await interaction.followup.send(
                f"{user.display_name} wasn't on the allowlist. "
                "(Admins and anyone with the configured role keep access regardless.)"
            )
            return
        logger.info("allowlist: %s removed %s (%s)", interaction.user, user, user.id)
        await interaction.followup.send(f"🚫 {user.display_name} can no longer use `/palworld`.")

    @group.command(name="allowed", description="Who can use the Palworld controls")
    async def allowed(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        if interaction.guild is None:
            await interaction.followup.send("Server-only command.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send(
                "Only server admins can view the allowlist.", ephemeral=True
            )
            return
        users = self.games.load_allowed()
        lines = []
        for uid in sorted(users):
            member = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
            lines.append(f"• {member.display_name if member else f'(left server) {uid}'}")
        body = "\n".join(lines) if lines else "_nobody yet_"
        extra = f"\nAnyone with the **{config.POWER_ROLE}** role also has access." if config.POWER_ROLE else ""
        await interaction.followup.send(
            f"**Allowed to use `/palworld`:**\n{body}\n"
            f"\nServer admins always have access and can change this list.{extra}",
            ephemeral=True,
        )

    @group.command(name="status", description="Is the Palworld server up?")
    async def status(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        refusal = await self._refuse(interaction)
        if refusal:
            await interaction.followup.send(refusal, ephemeral=True)
            return

        data, err = await self.games.status()
        if err:
            await interaction.followup.send(f"Couldn't check: {err}.")
            return
        if data.get("running"):
            await interaction.followup.send("🟢 Palworld is **up**.")
        else:
            await interaction.followup.send(
                "⚪ Palworld is **off** — it stops itself once everyone logs off. "
                "Use `/palworld start` to bring it back."
            )

    @group.command(name="players", description="Who's currently on the Palworld server?")
    async def players(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        refusal = await self._refuse(interaction)
        if refusal:
            await interaction.followup.send(refusal, ephemeral=True)
            return
        if not self.games.players_configured:
            await interaction.followup.send(
                "The player list isn't configured on this bot.", ephemeral=True
            )
            return

        # Check power first: with the server down the REST call just times out,
        # and "off" is a much more useful answer than a connection error.
        running, err = await self.games.is_running()
        if not err and not running:
            await interaction.followup.send(
                "⚪ Server is **off** — nobody's on. `/palworld start` to bring it up."
            )
            return

        people, err = await self.games.players()
        if err:
            await interaction.followup.send(
                f"Couldn't read the player list — {err}."
            )
            return
        if not people:
            await interaction.followup.send("🟢 Server is up, but **nobody's on** right now.")
            return
        lines = []
        for p in people:
            name = str(p.get("name") or "?")
            lvl = p.get("level")
            lines.append(f"• **{name}**" + (f" — level {lvl}" if lvl else ""))
        await interaction.followup.send(
            f"🟢 **{len(people)} online:**\n" + "\n".join(lines)
        )

    @group.command(name="connect", description="How to join: address and password")
    async def connect(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        refusal = await self._refuse(interaction)
        if refusal:
            await interaction.followup.send(refusal, ephemeral=True)
            return
        if not config.PALWORLD_CONNECT_HOST:
            await interaction.followup.send(
                "Connection details aren't configured on this bot.", ephemeral=True
            )
            return

        # Palworld's "Join with IP" box wants a literal address — a hostname is
        # rejected by the client, so handing out mc.example.com was wrong.
        # Look up the live public IP each time so this survives an ISP rotation
        # instead of going quietly stale. Note the bot cannot resolve the
        # hostname itself to get this: it resolves through AdGuard, which
        # wildcards *.example.com to the LAN address 192.168.1.176.
        addr = await self.games.connect_address()
        body = [
            "**Joining Palworld**",
            "PC: *Join Multiplayer Game* → **Join with IP**",
            f"Address: `{addr}`",
            f"Console (Xbox/PS5): search **{config.PALWORLD_SERVER_NAME or 'the server'}** "
            "in the community server list — consoles have no address box.",
        ]
        if config.PALWORLD_SERVER_PASSWORD:
            body.append(f"Password: `{config.PALWORLD_SERVER_PASSWORD}`")
        else:
            body.append("Password: _none_")
        running, err = await self.games.is_running()
        if not err:
            body.append(
                "\n🟢 Server is up." if running
                else "\n⚪ Server is off — `/palworld start` first."
            )
        # Ephemeral: this carries the server password, so it should not persist
        # in channel history for anyone who scrolls back.
        await interaction.followup.send("\n".join(body), ephemeral=True)

    @group.command(name="start", description="Start the Palworld server")
    async def start(self, interaction: discord.Interaction):
        if not await defer_interaction(interaction):
            return
        refusal = await self._refuse(interaction)
        if refusal:
            await interaction.followup.send(refusal, ephemeral=True)
            return

        # Already being started by someone else — say so and do nothing. Checked
        # before the status call on purpose: during the boot window the
        # container is not yet `running`, so a status check alone would let a
        # second caller through to POST /start again.
        # The guard lives on the service, so a start from any interface — not
        # just this one — blocks the others for the whole boot window.
        if not self.games.begin_start():
            await interaction.followup.send(
                "Palworld is already starting — hang tight. ⏳", ephemeral=True
            )
            return
        try:
            running, err = await self.games.is_running()
            if err:
                await interaction.followup.send(f"Couldn't reach the server controls: {err}.")
                return
            if running:
                await interaction.followup.send("Palworld is already up — jump in. 🟢")
                return

            logger.info("Palworld start requested by %s (%s)", interaction.user, interaction.user.id)
            err = await self.games.request_start()
            if err:
                await interaction.followup.send(f"Couldn't start it: {err}.")
                return

            await interaction.followup.send("Starting Palworld… ⏳")

            if await self.games.wait_until_running():
                # Container up != joinable. The world still has to load, and a
                # boot that pulls a game update takes minutes, so say so rather
                # than sending people to a server that refuses them.
                await interaction.followup.send(
                    "🟢 Palworld is **up**. Give it another 30–60s to accept "
                    "connections (longer if it's installing an update)."
                )
                return

            await interaction.followup.send(
                "Still starting — it's taking longer than usual, which usually "
                "means a game update is downloading. Try `/palworld status` in a "
                "few minutes."
            )
        finally:
            self.games.end_start()


async def setup(bot: commands.Bot):
    if not bot.games.enabled:
        logger.info("POWER_URL unset — games cog not loaded")
        return
    await bot.add_cog(GamesCog(bot, bot.games))
