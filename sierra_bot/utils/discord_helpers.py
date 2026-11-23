import logging

import discord

logger = logging.getLogger(__name__)


def can_send_embed(channel: discord.TextChannel, member: discord.Member | None) -> bool:
    if member is None:
        return False
    permissions = channel.permissions_for(member)
    return permissions.view_channel and permissions.send_messages and permissions.embed_links


async def reply_private(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.type is discord.InteractionResponseType.deferred_channel_message:
        await interaction.edit_original_response(content=content)
    elif interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def reply_with_error(interaction: discord.Interaction, content: str) -> None:
    try:
        await reply_private(interaction, content)
    except discord.HTTPException as error:
        logger.warning(
            "Could not respond to interaction %s (Discord status %s, code %s)",
            interaction.id,
            error.status,
            error.code,
        )
