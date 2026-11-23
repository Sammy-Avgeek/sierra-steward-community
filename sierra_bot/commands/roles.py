import discord
from discord import app_commands

from ..utils.discord_helpers import can_send_embed, reply_private

role_commands = app_commands.Group(name="roles", description="Choose your own server roles.")
RESTRICTED_PERMISSIONS = {
    "administrator", "kick_members", "ban_members", "moderate_members", "mention_everyone",
    "mute_members", "deafen_members", "move_members", "view_audit_log",
}


def has_staff_permissions(role: discord.Role) -> bool:
    return any(enabled and (name.startswith("manage_") or name in RESTRICTED_PERMISSIONS)
               for name, enabled in role.permissions)


def available_roles(bot, guild: discord.Guild) -> list[discord.Role]:
    roles = []
    if guild.me is None or not guild.me.guild_permissions.manage_roles:
        return roles
    ticket_roles = bot.settings.ticket_staff_role_ids | bot.tickets.retained_staff_role_ids
    for role_id in bot.settings.self_roles:
        role = guild.get_role(role_id)
        if (
            role is None or role.id in ticket_roles or role.id == bot.settings.member_role_id
            or not role.is_assignable()
        ):
            continue
        if has_staff_permissions(role):
            continue
        roles.append(role)
    return roles


class RolePicker(discord.ui.Select):
    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(
            placeholder="Choose a role to add or remove",
            options=[discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in roles],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if (guild is None or guild.id != interaction.client.settings.guild_id
                or not isinstance(interaction.user, discord.Member) or interaction.user.pending):
            await reply_private(interaction, "Complete membership screening before choosing roles.")
            return
        selected = int(self.values[0])
        role = next((role for role in available_roles(interaction.client, guild) if role.id == selected), None)
        if role is None:
            await reply_private(interaction, "This role is no longer available.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            member = await guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            await reply_private(interaction, "Discord could not look up your server membership. Try again.")
            return
        if member.pending:
            await reply_private(interaction, "Complete membership screening before choosing roles.")
            return
        removing = role in member.roles
        change_role = member.remove_roles if removing else member.add_roles
        try:
            await change_role(role, reason="Sierra Steward self-assigned roles")
        except discord.HTTPException:
            await reply_private(interaction, "Discord could not update that role. Ask staff to check access.")
            return
        state = "Removed" if removing else "Added"
        await reply_private(interaction, f"{state} **{discord.utils.escape_markdown(role.name)}**.")


async def show_roles(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None or guild.id != interaction.client.settings.guild_id:
        await reply_private(interaction, "Choose roles from inside the configured server.")
        return
    roles = available_roles(interaction.client, guild)
    if not roles:
        await reply_private(interaction, "No self-assignable roles are configured.")
        return
    view = discord.ui.View(timeout=180)
    view.add_item(RolePicker(roles))
    await interaction.response.send_message("Choose a role to add or remove.", view=view, ephemeral=True)


class RolePanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Choose roles", style=discord.ButtonStyle.primary, custom_id="sierra:roles:choose")
    async def choose_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await show_roles(interaction)


@role_commands.command(name="choose", description="Add or remove a self-assignable role.")
async def choose_roles(interaction: discord.Interaction) -> None:
    await show_roles(interaction)


@role_commands.command(name="panel", description="Post the self-assignable roles button in this channel.")
@app_commands.checks.has_permissions(manage_roles=True)
async def post_roles(interaction: discord.Interaction) -> None:
    guild, channel = interaction.guild, interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel) or not can_send_embed(channel, guild.me):
        await reply_private(interaction, "Choose a channel where the bot can send embeds.")
        return
    if not available_roles(interaction.client, guild):
        await reply_private(interaction, "Configure at least one regular role below the bot’s role first.")
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    message = await channel.send(
        embed=discord.Embed(
            title="Choose your roles", description="Use **Choose roles** to add or remove an available role.",
            color=discord.Color.blurple(),
        ),
        view=RolePanel(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    await reply_private(interaction, f"[Open the role panel]({message.jump_url})")
