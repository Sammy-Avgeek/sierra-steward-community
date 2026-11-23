import asyncio
import logging
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands

from ..config import Settings
from ..storage import Warning
from ..utils.discord_helpers import can_send_embed, reply_private

if TYPE_CHECKING:
    from ..bot import SierraBot

logger = logging.getLogger(__name__)


def mod_log_channel(guild: discord.Guild, settings: Settings) -> discord.TextChannel | None:
    channel = guild.get_channel(settings.mod_log_channel_id)
    if not isinstance(channel, discord.TextChannel) or not can_send_embed(channel, guild.me):
        return None
    member_role = guild.get_role(settings.member_role_id)
    if channel.permissions_for(guild.default_role).view_channel:
        return None
    if member_role and channel.permissions_for(member_role).view_channel:
        return None
    return channel


@app_commands.command(name="warn", description="Record and send a warning to a server member.")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(user="Member to warn", reason="Reason for the warning")
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: app_commands.Range[str, 1, 1000],
) -> None:
    guild = interaction.guild
    channel_id = interaction.channel_id
    moderator = interaction.user
    reason = reason.strip()
    if guild is None or channel_id is None or not isinstance(moderator, discord.Member):
        await reply_private(interaction, "Use this command inside the server.")
        return
    if not reason:
        await reply_private(interaction, "Enter a reason for the warning.")
        return
    if user.id == moderator.id or user.id == guild.owner_id or user.bot:
        await reply_private(interaction, "Choose another member. The server owner and bots are exempt.")
        return
    if moderator.id != guild.owner_id and user.top_role >= moderator.top_role:
        await reply_private(interaction, "You can warn members whose highest role is below yours.")
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    client = cast("SierraBot", interaction.client)
    warning_id, dm_status, log_status = await record_and_notify(
        client,
        user,
        Warning(
            interaction_id=interaction.id,
            guild_id=guild.id,
            channel_id=channel_id,
            user_id=user.id,
            moderator_id=moderator.id,
            reason=reason,
            created_at=interaction.created_at,
        ),
        f"{moderator.mention} ({moderator.id})",
    )
    if dm_status is None:
        await reply_private(interaction, f"This interaction already saved warning #{warning_id}.")
        return

    await reply_private(
        interaction, f"Saved warning **#{warning_id}**.\nDM — {dm_status}\nMod log — {log_status}"
    )


async def record_and_notify(
    client: "SierraBot", user: discord.Member, warning: Warning, actor_label: str
) -> tuple[int, str | None, str | None]:
    warning_id, created = await client.warnings.record(warning)
    if not created:
        return warning_id, None, None

    # Save before either external call so blocked DMs or channel errors cannot lose the record.
    guild = user.guild
    description = discord.utils.escape_markdown(warning.reason, ignore_links=False)
    notice = discord.Embed(
        title="Server warning",
        description=description,
        color=discord.Color.orange(),
        timestamp=warning.created_at,
    )
    notice.add_field(name="Server", value=discord.utils.escape_markdown(guild.name))
    notice.set_footer(text=f"Warning #{warning_id}")
    dm_status = "Sent"
    try:
        async with asyncio.timeout(20):
            await user.send(embed=notice)
    except discord.Forbidden:
        dm_status = "Unavailable or blocked"
    except Exception:
        dm_status = "Delivery unconfirmed"
        logger.exception("Could not confirm DM for warning %s", warning_id)

    channel = mod_log_channel(guild, client.settings)
    log_status = "Sent"
    if channel is None:
        log_status = "Channel unavailable or misconfigured"
        logger.warning("No writable private mod-log channel for warning %s", warning_id)
    else:
        entry = discord.Embed(
            title=f"Warning #{warning_id}",
            description=description,
            color=discord.Color.orange(),
            timestamp=warning.created_at,
        )
        entry.add_field(name="Member", value=f"{user.mention} ({user.id})")
        entry.add_field(name="Moderator", value=actor_label)
        entry.add_field(name="DM", value=dm_status)
        try:
            async with asyncio.timeout(20):
                await channel.send(embed=entry, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log_status = "Delivery unconfirmed"
            logger.exception("Could not confirm mod-log delivery for warning %s", warning_id)

    return warning_id, dm_status, log_status
