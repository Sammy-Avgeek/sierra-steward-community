import logging
from importlib.resources import files

import discord

from ..config import Settings
from ..message_style import MessageStyle, render_description
from ..utils.discord_helpers import can_send_embed

logger = logging.getLogger(__name__)


def member_message_channel(guild: discord.Guild, channel_id: int) -> discord.TextChannel | None:
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) and can_send_embed(channel, guild.me) else None


def welcome_resources(
    guild: discord.Guild, settings: Settings
) -> tuple[discord.TextChannel | None, discord.Role | None]:
    channel = member_message_channel(guild, settings.welcome_channel_id) if settings.welcome_enabled else None
    bot_member = guild.me
    role = guild.get_role(settings.member_role_id)
    if (
        role is None
        or bot_member is None
        or not bot_member.guild_permissions.manage_roles
        or role.permissions.administrator
        or not role.is_assignable()
    ):
        role = None
    return channel, role


async def welcome_member(member: discord.Member, settings: Settings) -> None:
    if member.guild.id != settings.guild_id or member.bot or member.pending:
        return

    channel, role = welcome_resources(member.guild, settings)
    if role is None:
        logger.warning("Configured Member role unavailable for member %s", member.id)
    elif role not in member.roles:
        try:
            await member.add_roles(role, reason="Community Console onboarding")
        except discord.NotFound as error:
            if error.code == 10007:  # The member left before Discord processed the request.
                return
            logger.warning("Role assignment failed for member %s (code %s)", member.id, error.code)
        except Exception:
            logger.exception("Role assignment failed for member %s", member.id)

    if not settings.welcome_enabled:
        return
    if channel is None:
        logger.warning("Configured welcome channel unavailable for member %s", member.id)
        return

    await send_member_message(channel, settings.welcome_style, member, member.mention, "welcome")


async def goodbye_member(guild: discord.Guild, member: discord.User | discord.Member, settings: Settings) -> None:
    if not settings.goodbye_enabled or guild.id != settings.guild_id or member.bot:
        return
    if isinstance(member, discord.Member) and member.pending:
        return
    channel = member_message_channel(guild, settings.goodbye_destination_id)
    if channel is None:
        logger.warning("Configured goodbye channel unavailable for member %s", member.id)
        return
    name = discord.utils.escape_mentions(discord.utils.escape_markdown(member.display_name, ignore_links=False))
    await send_member_message(channel, settings.goodbye_style, member, name, "goodbye")


def build_member_message(style: MessageStyle, server_name: str, display: str) -> discord.Embed:
    server = discord.utils.escape_mentions(discord.utils.escape_markdown(server_name, ignore_links=False))
    embed = discord.Embed(title=style.title, color=style.color,
                          description=render_description(style.description, member=display, server=server))
    if style.image_url and style.image_url != "off":
        embed.set_image(url=style.image_url)
    return embed


async def send_member_message(
    channel: discord.TextChannel, style: MessageStyle, member: discord.User | discord.Member, display: str, event: str
) -> None:
    embed = build_member_message(style, channel.guild.name, display)
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        if not style.image_url and channel.permissions_for(channel.guild.me).attach_files:
            filename = f"{event}.png"
            try:
                with files(__package__).joinpath("banners", filename).open("rb") as stream:
                    embed.set_image(url=f"attachment://{filename}")
                    await channel.send(embed=embed, file=discord.File(stream, filename=filename),
                                       allowed_mentions=discord.AllowedMentions.none())
                return
            except FileNotFoundError:
                logger.warning("The bundled %s banner is unavailable.", event)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("%s message failed for member %s", event, member.id)
