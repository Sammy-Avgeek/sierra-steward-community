import logging
import re
from urllib.parse import urlsplit

import discord
from discord import app_commands

from ..utils.discord_helpers import can_send_embed, reply_private, reply_with_error

logger = logging.getLogger(__name__)
DESTINATION_ERROR = (
    "Choose a server text channel where you can view, send, embed links, and manage messages. "
    "The bot needs to be able to view the channel, send messages, and embed links there."
)
PREVIEW_ERROR = "The bot needs Embed Links in this channel to show the private preview."


def publish_channel(interaction: discord.Interaction, channel_id: int) -> discord.TextChannel | None:
    guild = interaction.guild
    user = interaction.user
    if guild is None or not isinstance(user, discord.Member):
        return None
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return None
    if not can_send_embed(channel, user) or not channel.permissions_for(user).manage_messages:
        return None
    return channel if can_send_embed(channel, guild.me) else None


def build_embed(
    title: str, description: str, color: str, footer: str, image: str
) -> discord.Embed:
    if not title and not description:
        raise ValueError("Enter a title or description.")
    if len(title) > 256 or len(description) > 4000 or len(footer) > 512 or len(image) > 2048:
        raise ValueError("Use up to 256 characters in the title, 4,000 in the description, 512 in the footer, and 2,048 in the image URL.")

    accent = discord.Color.blurple()
    if color:
        match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", color)
        if not match:
            raise ValueError("Enter a six-digit hex color, such as #5865F2.")
        accent = discord.Color(int(match[1], 16))

    if image:
        try:
            url = urlsplit(image)
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and not any(character.isspace() or ord(character) < 32 for character in image)
            )
            url.port  # Access validates the port if the URL specifies one.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Enter a public HTTPS image URL.")

    embed = discord.Embed(title=title or None, description=description or None, color=accent)
    if footer:
        embed.set_footer(text=footer)
    if image:
        embed.set_image(url=image)
    return embed


class PublishView(discord.ui.View):
    def __init__(self, author_id: int, channel_id: int, draft_id: int, embed: discord.Embed) -> None:
        super().__init__(timeout=300)
        self.author_id = author_id
        self.channel_id = channel_id
        self.draft_id = draft_id
        self.embed = embed
        self.publishing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id or not interaction.permissions.manage_messages:
            await reply_private(interaction, "You need Manage Messages to use your own draft.")
            return False
        return True

    @discord.ui.button(label="Publish embed", style=discord.ButtonStyle.primary)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.is_finished():
            await reply_private(interaction, "This draft is no longer available to publish.")
            return
        if self.publishing:
            await reply_private(interaction, "This draft is already being published.")
            return
        channel = publish_channel(interaction, self.channel_id)
        if channel is None:
            await reply_private(interaction, DESTINATION_ERROR)
            return

        # Claim the draft before the first await. The nonce also deduplicates Discord HTTP retries.
        self.publishing = True
        try:
            await interaction.response.defer()
        except Exception:
            self.publishing = False
            raise

        try:
            message = await channel.send(
                embed=self.embed,
                allowed_mentions=discord.AllowedMentions.none(),
                nonce=str(self.draft_id),
            )
        except (discord.Forbidden, discord.NotFound):
            self.publishing = False
            await reply_private(interaction, "The bot lost access to the destination channel.")
            return
        except Exception:
            # A transport failure can follow a successful send. Close the draft to avoid a repost.
            self.stop()
            logger.exception("Could not confirm delivery for embed draft %s", self.draft_id)
            await interaction.edit_original_response(
                content="Delivery could not be confirmed. Check the destination before posting again.",
                embed=None,
                view=None,
            )
            return

        self.stop()
        await interaction.edit_original_response(
            content=f"[View the published embed]({message.jump_url})", embed=None, view=None
        )

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.publishing or self.is_finished():
            await reply_private(interaction, "This draft is no longer available to edit.")
            return
        await interaction.response.send_modal(EmbedModal(self.channel_id, previous=self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.publishing or self.is_finished():
            await reply_private(interaction, "This draft is no longer available to cancel.")
            return
        self.publishing = True
        try:
            await interaction.response.edit_message(content="Embed draft discarded.", embed=None, view=None)
        except Exception:
            self.publishing = False
            raise
        self.stop()

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.error("Embed preview action failed for draft %s", self.draft_id, exc_info=error)
        await reply_with_error(interaction, "The action could not be completed. Check the destination.")


class EmbedModal(discord.ui.Modal, title="Make an embed"):
    def __init__(self, channel_id: int, previous: PublishView | None = None) -> None:
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.previous = previous
        draft = previous.embed if previous else discord.Embed()
        color = f"#{draft.color.value:06X}" if draft.color else None
        self.title_input = discord.ui.TextInput(default=draft.title, required=False, max_length=256)
        self.description = discord.ui.TextInput(
            default=draft.description, style=discord.TextStyle.paragraph, required=False, max_length=4000
        )
        self.color = discord.ui.TextInput(
            default=color, placeholder="#5865F2", required=False, max_length=7
        )
        self.footer = discord.ui.TextInput(
            default=draft.footer.text, required=False, max_length=512
        )
        self.image = discord.ui.TextInput(
            default=draft.image.url, placeholder="https://example.com/image.png",
            required=False, max_length=2048,
        )
        self.add_item(discord.ui.Label(text="Title", component=self.title_input))
        self.add_item(discord.ui.Label(text="Description", component=self.description))
        self.add_item(discord.ui.Label(text="Hex color (optional)", component=self.color))
        self.add_item(discord.ui.Label(text="Footer (optional)", component=self.footer))
        self.add_item(discord.ui.Label(text="Image URL (optional)", component=self.image))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.manage_messages:
            await reply_private(interaction, "You need Manage Messages to use the embed maker.")
            return
        if not interaction.app_permissions.embed_links:
            await reply_private(interaction, PREVIEW_ERROR)
            return
        channel = publish_channel(interaction, self.channel_id)
        if channel is None:
            await reply_private(interaction, DESTINATION_ERROR)
            return
        try:
            embed = build_embed(
                self.title_input.value.strip(),
                self.description.value.strip(),
                self.color.value.strip(),
                self.footer.value.strip(),
                self.image.value.strip(),
            )
        except ValueError as error:
            retry = "Choose **Edit** on your preview." if self.previous else "Open `/embed` to try again."
            await reply_private(interaction, f"{error} {retry}")
            return

        content = f"Private preview for {channel.mention}. Publish within five minutes."
        previous = self.previous
        draft_id = previous.draft_id if previous else interaction.id
        view = PublishView(interaction.user.id, channel.id, draft_id, embed)
        if previous is None:
            await interaction.response.send_message(
                content, embed=embed, view=view, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if previous.publishing or previous.is_finished():
            await reply_private(interaction, "This preview changed or expired. Use the latest preview.")
            return
        # A submit from an old edit form cannot overwrite a published or cancelled preview.
        previous.publishing = True
        try:
            await interaction.response.edit_message(
                content=content, embed=embed, view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            previous.publishing = False
            raise
        previous.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error("Embed form failed for interaction %s", interaction.id, exc_info=error)
        await reply_with_error(interaction, "Could not show the preview. Run `/embed` to try again.")


@app_commands.command(name="embed", description="Create, preview, and publish an embed.")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(channel="Text channel to publish to. Defaults to this channel.")
async def make_embed(
    interaction: discord.Interaction, channel: discord.TextChannel | None = None
) -> None:
    if not interaction.app_permissions.embed_links:
        await reply_private(interaction, PREVIEW_ERROR)
        return
    channel_id = channel.id if channel is not None else interaction.channel_id
    if channel_id is None or publish_channel(interaction, channel_id) is None:
        await reply_private(interaction, DESTINATION_ERROR)
        return
    await interaction.response.send_modal(EmbedModal(channel_id))
