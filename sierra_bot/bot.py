import logging
import sqlite3

import discord
from discord import app_commands

from .commands.briefing import brief_flight
from .commands.embeds import make_embed
from .commands.general import ping
from .commands.moderation import mod_log_channel, warn
from .commands.roles import RolePanel, role_commands
from .commands.tickets import TicketPanel, TicketTypesPanel, ticket_commands
from .config import ConfigError, Settings
from .events.creator_alerts import CreatorAlerts
from .events.spam_trap import SpamTrap
from .events.welcome import goodbye_member, member_message_channel, welcome_member, welcome_resources
from .storage import WarningStore
from .tickets import TicketError, Tickets
from .utils.discord_helpers import reply_with_error

logger = logging.getLogger(__name__)
SLASH_COMMANDS = (ping, warn, make_embed, ticket_commands, role_commands, brief_flight)


async def handle_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(permission.replace("_", " ").title() for permission in error.missing_permissions)
        message = f"You need {missing} to use this command."
    elif isinstance(error, app_commands.TransformerError):
        message = "Discord could not resolve that member or channel. Check your selection and try again."
    else:
        cause = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        if isinstance(cause, TicketError):
            message = str(cause)
        else:
            logger.error("Slash command failed for interaction %s", interaction.id, exc_info=cause)
            if isinstance(cause, sqlite3.Error):
                message = "SQLite could not confirm the operation. Ask an admin to check the bot logs."
            else:
                message = "The command could not be completed. Ask an admin to check the bot logs."
    await reply_with_error(interaction, message)


class SierraBot(discord.Client):
    def __init__(
        self, settings: Settings, *, command_registrations: set[tuple[int, int]] | None = None
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.guild_messages = settings.trap_channel_id is not None
        super().__init__(
            intents=intents,
            application_id=settings.client_id,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.command_registrations = command_registrations
        self.warnings = WarningStore(settings.database_path)
        self.tickets = Tickets(settings)
        self.trap = SpamTrap(settings)
        self.alerts = CreatorAlerts(self)
        self.target_guild = discord.Object(id=settings.guild_id)
        self.tree = app_commands.CommandTree(self)
        self.tree.error(handle_command_error)
        for command in SLASH_COMMANDS:
            self.tree.add_command(command, guild=self.target_guild)

    async def setup_hook(self) -> None:
        if self.application is None or self.application.id != self.settings.client_id:
            raise ConfigError("CLIENT_ID must match the Application ID for the supplied bot token.")

        await self.warnings.initialize()
        await self.tickets.initialize()
        await self.alerts.initialize()
        self.add_view(TicketPanel(self.settings.ticket_button_label, self.settings.ticket_button_style))
        self.add_view(TicketTypesPanel(self.settings))
        self.add_view(RolePanel())
        registration = (self.settings.client_id, self.settings.guild_id)
        if self.command_registrations is not None and registration in self.command_registrations:
            logger.info("Using existing Discord slash commands.")
            return
        # Register guild commands before the Gateway starts. A desktop restart uses the same definitions.
        commands = await self.tree.sync(guild=self.target_guild)
        if self.command_registrations is not None:
            self.command_registrations.add(registration)
        logger.info("Synced %s commands to server %s", len(commands), self.target_guild.id)

    async def on_ready(self) -> None:
        logger.info("Connected as %s", self.user)
        guild = self.get_guild(self.target_guild.id)
        if guild is None or guild.unavailable:
            logger.warning("Server %s is unavailable", self.target_guild.id)
            return

        channel, role = welcome_resources(guild, self.settings)
        if self.settings.welcome_enabled and channel is None:
            logger.warning("Check WELCOME_CHANNEL_ID and the bot's channel permissions")
        if self.settings.goodbye_enabled and member_message_channel(guild, self.settings.goodbye_destination_id) is None:
            logger.warning("Check GOODBYE_CHANNEL_ID and the bot's channel permissions")
        if role is None:
            logger.warning("Check MEMBER_ROLE_ID, Manage Roles, and the bot's position in the role list")
        if mod_log_channel(guild, self.settings) is None:
            logger.warning("Check MOD_LOG_CHANNEL_ID, channel privacy, and the bot's permissions")
        self.alerts.start()

    async def close(self) -> None:
        try:
            await self.alerts.stop()
        finally:
            await super().close()

    async def on_message(self, message: discord.Message) -> None:
        if self.settings.trap_channel_id is not None:
            await self.trap.observe(message)

    async def on_member_join(self, member: discord.Member) -> None:
        await welcome_member(member, self.settings)

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.pending and not after.pending:
            await welcome_member(after, self.settings)

    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        guild = self.get_guild(payload.guild_id)
        if guild is not None and not guild.unavailable:
            await goodbye_member(guild, payload.user, self.settings)
