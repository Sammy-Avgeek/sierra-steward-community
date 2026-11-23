import asyncio

import aiohttp
import discord

from .bot import SLASH_COMMANDS
from .commands.roles import has_staff_permissions
from .config import Settings

EMBED_PERMISSIONS = ("view_channel", "send_messages", "embed_links")


def missing_permissions(permissions: discord.Permissions, required: tuple[str, ...]) -> str:
    return ", ".join(
        name.replace("_", " ").title() for name in required if not getattr(permissions, name)
    )


def channel_problem(channel, channel_id: int, bot: discord.Member, required: tuple[str, ...]) -> str:
    if channel is None:
        return f"Channel {channel_id} is unavailable. Verify the text channel ID and grant the bot View Channel."
    if not isinstance(channel, discord.TextChannel):
        return f"#{channel.name} is not a text or announcement channel. Choose a supported channel in Settings."
    missing = missing_permissions(channel.permissions_for(bot), required)
    return f"Grant the bot {missing} in #{channel.name}." if missing else ""


def role_problem(role, role_id: int, bot: discord.Member) -> str:
    if role is None:
        return f"Role {role_id} was not found. Verify the saved role ID."
    if role.managed or role.is_default():
        return f"Choose a regular server role in place of {role.name}."
    if not bot.guild_permissions.manage_roles:
        return "Grant the bot Manage Roles."
    # REST fetches return a detached guild without the gateway's cached `guild.me`.
    if bot.id != bot.guild.owner_id and role >= bot.top_role:
        return f"Move the bot's role above {role.name} in Server Settings → Roles."
    return ""


def result(label: str, detail: str, problems: list[str]) -> str:
    issues = " ".join(dict.fromkeys(problem for problem in problems if problem))
    return f"Needs attention · {label}\n{issues}" if issues else f"OK · {label}\n{detail}"


def check_permissions(
    settings: Settings, guild: discord.Guild, bot: discord.Member, channels: dict[int, discord.abc.GuildChannel]
) -> list[str]:
    member_role = guild.get_role(settings.member_role_id)
    mod_log = channels.get(settings.mod_log_channel_id)
    member_problem = role_problem(member_role, settings.member_role_id, bot)
    if member_role and member_role.permissions.administrator:
        member_problem = "Choose a default member role without Administrator."
    log_problems = [channel_problem(mod_log, settings.mod_log_channel_id, bot, EMBED_PERMISSIONS)]
    if isinstance(mod_log, discord.TextChannel):
        visible_roles = [role.name for role in (guild.default_role, member_role)
                         if role is not None and mod_log.permissions_for(role).view_channel]
        if visible_roles:
            log_problems.append(f"Hide #{mod_log.name} from {' and '.join(visible_roles)} to enable staff logging.")

    lines = []
    for label, enabled, destination in (
        ("Welcome messages", settings.welcome_enabled, settings.welcome_channel_id),
        ("Goodbye messages", settings.goodbye_enabled, settings.goodbye_destination_id),
        ("Flight sim news", settings.flight_news_channel_id is not None, settings.flight_news_channel_id),
        ("Special offers", settings.special_offers_channel_id is not None, settings.special_offers_channel_id),
    ):
        if enabled:
            channel = channels.get(destination)
            lines.append(result(label, f"Can post embeds in #{getattr(channel, 'name', '')}.", [
                channel_problem(channel, destination, bot, EMBED_PERMISSIONS),
            ]))
        else:
            lines.append(f"Off · {label}\nTurn on the feature in Settings to use it.")
    lines += [
        result("Default member role", f"Can assign {getattr(member_role, 'name', '')}.", [member_problem]),
        result("Staff log", f"Can post in #{getattr(mod_log, 'name', '')}. Hidden from @everyone and the default member role.", log_problems),
    ]
    desktop_warn = missing_permissions(bot.guild_permissions, ("manage_messages",))
    lines.append(result("Desktop warnings", "The bot has Manage Messages.", [
        f"Grant the bot {desktop_warn} to issue warnings from the desktop app." if desktop_warn else "",
    ]))

    if settings.self_roles:
        problems = []
        for role_id in settings.self_roles:
            role = guild.get_role(role_id)
            if role_id == settings.member_role_id or (role is not None and has_staff_permissions(role)):
                problems.append(f"Remove {role.name if role else role_id} from self-assignable roles in Settings.")
            else:
                problems.append(role_problem(role, role_id, bot))
        lines.append(result("Role picker", f"{len(settings.self_roles)} configured roles pass the Discord permission checks.", problems))
    else:
        lines.append("Off · Role picker\nNo self-assignable roles are configured.")

    ticket_management = missing_permissions(bot.guild_permissions, ("manage_channels", "manage_roles"))
    ticket_problems = [f"Grant the bot {ticket_management} to create private tickets." if ticket_management else ""]
    categories = {kind.category_id or settings.ticket_category_id for kind in settings.ticket_types}
    if not settings.ticket_types:
        categories.add(settings.ticket_category_id)
    for category_id in sorted(categories - {None}):
        if not isinstance(channels.get(category_id), discord.CategoryChannel):
            ticket_problems.append(f"Ticket category {category_id} is unavailable. Verify the category and the bot's access.")
    for role_id in sorted(settings.ticket_staff_role_ids):
        role = guild.get_role(role_id)
        if role is None or role.managed:
            ticket_problems.append(f"Choose a regular staff role in place of unavailable ticket role {role_id}.")
    lines.append(result("Tickets", "The bot has ticket management permissions and the saved routing targets are available.", ticket_problems))

    if settings.youtube_channels:
        alerts = channels.get(settings.alert_channel_id)
        lines.append(result("YouTube alerts", f"Can post alerts in #{getattr(alerts, 'name', '')}.", [
            channel_problem(alerts, settings.alert_channel_id, bot, EMBED_PERMISSIONS),
        ]))
    else:
        lines.append("Off · YouTube alerts\nNo YouTube feeds are configured.")
    if settings.trap_channel_id is not None:
        trap = channels.get(settings.trap_channel_id)
        problems = [channel_problem(trap, settings.trap_channel_id, bot, ("view_channel",))]
        if settings.trap_timeout_minutes and not bot.guild_permissions.moderate_members:
            problems.append("Grant the bot Moderate Members to use automatic timeouts.")
        if any(log_problems):
            problems.append("Restore the private staff log to deliver alerts.")
        lines.append(result("Spam-trap alerts", f"Can observe #{getattr(trap, 'name', '')} and post to the staff log.", problems))
    else:
        lines.append("Off · Spam-trap alerts\nNo channel is configured.")
    return lines


async def check_server(settings: Settings, token: str) -> str:
    """Inspect saved settings and current Discord permissions without opening a gateway session."""
    try:
        async with asyncio.timeout(20), discord.Client(intents=discord.Intents.none()) as client:
            await client.login(token)
            if client.application_id != settings.client_id:
                return "Needs attention · Bot identity\nThe saved Application ID does not match the bot token. Check Settings → Discord."
            try:
                guild = await client.fetch_guild(settings.guild_id)
                bot = await guild.fetch_member(client.user.id)
            except (discord.Forbidden, discord.NotFound):
                return ("Needs attention · Server membership\n"
                        f"Discord could not find the bot in server {settings.guild_id}, or access was denied. "
                        "Verify the saved Server ID and install the bot in that server.")

            lines = [f"OK · Server membership\n{bot.name} is installed in {guild.name}."]
            try:
                channels = await guild.fetch_channels()
            except discord.HTTPException:
                lines.append("Unverified · Server permissions\nDiscord did not return the server's channels.")
            else:
                by_id = {channel.id: channel for channel in channels}
                lines.extend(check_permissions(settings, guild, bot, by_id))
            try:
                commands = await discord.app_commands.CommandTree(client).fetch_commands(guild=guild)
            except discord.HTTPException:
                lines.append("Unverified · Slash commands\nDiscord did not return the server's command list.")
            else:
                registered = {command.name for command in commands if command.type is discord.AppCommandType.chat_input}
                missing = sorted({command.name for command in SLASH_COMMANDS} - registered)
                lines.append(result("Slash commands", "All Sierra Steward commands are registered in this server.", [
                    f"Missing {', '.join('/' + name for name in missing)}. Reopen the app and start the bot to sync commands."
                    if missing else "",
                ]))
            return "\n\n".join(lines)
    except discord.LoginFailure:
        return "Needs attention · Bot token\nDiscord rejected the saved token. Check Settings → Discord."
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError):
        return "Unverified · Discord connection\nThe request could not finish. Check the network connection and try again."
