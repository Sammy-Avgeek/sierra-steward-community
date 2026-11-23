import logging

import discord
from discord import app_commands

from ..config import Settings
from ..message_style import MESSAGE_DEFAULTS
from ..ticket_config import TICKET_FORM_QUESTION, TICKET_FORM_TITLE, TICKET_SELECT_PROMPT, TicketType
from ..ticket_json import styled_panel
from ..tickets import TicketError
from ..utils.discord_helpers import can_send_embed, reply_private

logger = logging.getLogger(__name__)
ticket_commands = app_commands.Group(name="ticket", description="Open and manage private support tickets.")


async def open_ticket(interaction: discord.Interaction, subject: str, kind: str | None = None) -> None:
    if not isinstance(interaction.user, discord.Member):
        await reply_private(interaction, "Open a support ticket from inside the server.")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        channel, created = await interaction.client.tickets.open(interaction.user, subject, kind)
    except TicketError as error:
        await reply_private(interaction, str(error))
    except discord.HTTPException:
        logger.exception("Discord could not create or recover a support ticket.")
        await reply_private(interaction, "Discord could not confirm your ticket. Try again or contact staff.")
    else:
        state = "Your ticket is ready" if created else "Your existing ticket is here"
        await reply_private(interaction, f"{state} {channel.mention}.")


class TicketForm(discord.ui.Modal, title="Open a support ticket"):
    def __init__(self, settings: Settings | None = None, kind: TicketType | None = None) -> None:
        super().__init__(title=settings.ticket_form_title if settings else TICKET_FORM_TITLE, timeout=180)
        self.kind = kind.key if kind else None
        question = (kind.question if kind else "") or (settings.ticket_form_question if settings else TICKET_FORM_QUESTION)
        self.subject = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=300, required=True)
        self.add_item(discord.ui.Label(text=question, component=self.subject))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await open_ticket(interaction, self.subject.value, self.kind)


class TicketPanel(discord.ui.View):
    def __init__(self, label: str = MESSAGE_DEFAULTS["TICKET_BUTTON_LABEL"], style: str = "primary") -> None:
        super().__init__(timeout=None)
        self.open_button.label = label
        self.open_button.style = getattr(discord.ButtonStyle, style)

    @discord.ui.button(label="Open ticket", style=discord.ButtonStyle.primary, custom_id="sierra:ticket:open")
    async def open_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        settings = interaction.client.settings
        if settings.ticket_types:
            await interaction.response.send_message(settings.ticket_select_prompt, view=TicketTypesPanel(settings, timeout=180), ephemeral=True)
        else:
            await interaction.response.send_modal(TicketForm(settings))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            custom_id="sierra:ticket:type", placeholder=settings.ticket_select_prompt or TICKET_SELECT_PROMPT,
            options=[discord.SelectOption(label=kind.name, value=kind.key, description=kind.description or None)
                     for kind in settings.ticket_types],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        kind = interaction.client.tickets.ticket_type(self.values[0])
        if kind is None:
            await reply_private(interaction, "This ticket type is no longer available. Ask staff for the current support panel.")
            return
        await interaction.response.send_modal(TicketForm(interaction.client.settings, kind))


class TicketTypesPanel(discord.ui.View):
    def __init__(self, settings: Settings, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.add_item(TicketTypeSelect(settings))


@ticket_commands.command(name="open", description="Open a private support channel.")
@app_commands.describe(kind="Choose a ticket type if the server has multiple options.")
async def open_command(interaction: discord.Interaction, subject: app_commands.Range[str, 1, 300], kind: str | None = None) -> None:
    if kind is not None and interaction.client.tickets.ticket_type(kind) is None:
        folded = kind.strip().casefold()
        kind = next((entry.key for entry in interaction.client.settings.ticket_types if entry.name.casefold() == folded), kind)
    await open_ticket(interaction, subject, kind)


@open_command.autocomplete("kind")
async def ticket_kind_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=kind.name, value=kind.key) for kind in interaction.client.settings.ticket_types
            if current.casefold() in kind.name.casefold()]


@ticket_commands.command(name="close", description="Archive the current ticket and retain its messages.")
async def close_command(
    interaction: discord.Interaction, reason: app_commands.Range[str, 1, 300] | None = None
) -> None:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member) or interaction.channel_id is None:
        await reply_private(interaction, "Use this command from inside your ticket.")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    store = interaction.client.tickets
    records = await store.select("channel_id = ?", (str(interaction.channel_id),))
    if not records:
        await reply_private(interaction, "Use this command inside a Sierra Steward ticket.")
        return
    if not store.member_can_close(interaction.user, records[0]):
        await reply_private(interaction, "Ask the assigned ticket staff to close this ticket.")
        return
    await store.close(guild, records[0], reason or "")
    await reply_private(interaction, f"Archived ticket #{records[0].id}. Its messages are still in the channel.")


@ticket_commands.command(name="panel", description="Post the current support panel in this channel.")
@app_commands.checks.has_permissions(manage_channels=True)
async def post_panel(interaction: discord.Interaction) -> None:
    channel, guild = interaction.channel, interaction.guild
    if guild is None or not isinstance(channel, discord.TextChannel) or not can_send_embed(channel, guild.me):
        await reply_private(interaction, "Choose a server text channel where the bot can send embeds.")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    message = await send_ticket_panel(channel, interaction.client.settings)
    await reply_private(interaction, f"[Open the ticket panel]({message.jump_url})")


def build_ticket_panel(settings: Settings, server_name: str) -> discord.Embed:
    label = settings.ticket_select_prompt if settings.ticket_types else settings.ticket_button_label
    return styled_panel(settings.ticket_panel_style, settings.ticket_panel_details, server_name, label)


async def send_ticket_panel(
    channel: discord.TextChannel, settings: Settings, existing: discord.Message | None = None,
) -> discord.Message:
    panel = TicketTypesPanel(settings) if settings.ticket_types else TicketPanel(settings.ticket_button_label, settings.ticket_button_style)
    publish = existing.edit if existing is not None else channel.send
    return await publish(embed=build_ticket_panel(settings, channel.guild.name), view=panel,
                         allowed_mentions=discord.AllowedMentions.none())


async def find_ticket_panel(channel: discord.TextChannel, bot_id: int) -> discord.Message | None:
    panel_ids = {"sierra:ticket:open", "sierra:ticket:type"}
    async for message in channel.history(limit=100):
        if message.author.id == bot_id and any(
            getattr(component, "custom_id", None) in panel_ids
            for row in message.components for component in getattr(row, "children", ())
        ):
            return message
    return None
