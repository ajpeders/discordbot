import logging
from datetime import timezone
from typing import Optional

import discord

logger = logging.getLogger(__name__)

_DEFER_STALE_MS = 2800


def interaction_age_ms(interaction: discord.Interaction) -> Optional[int]:
    if not interaction.created_at:
        return None
    now = discord.utils.utcnow().replace(tzinfo=timezone.utc)
    created = interaction.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return int((now - created).total_seconds() * 1000)


async def defer_interaction(interaction: discord.Interaction, thinking: bool = True) -> bool:
    """Defer an interaction, dropping it if already stale. Returns True on success."""
    age_ms = interaction_age_ms(interaction)
    cmd = interaction.command.qualified_name if interaction.command else "unknown"
    if age_ms is not None and age_ms > _DEFER_STALE_MS:
        logger.warning("Dropping stale /%s interaction age_ms=%s", cmd, age_ms)
        return False
    try:
        await interaction.response.defer(thinking=thinking)
        return True
    except discord.InteractionResponded:
        return True
    except discord.HTTPException as exc:
        logger.warning("Failed to defer /%s age_ms=%s: HTTP %s code=%s %r", cmd, age_ms, exc.status, exc.code, exc.text)
        return False
