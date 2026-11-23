import re
from datetime import UTC, datetime

import discord
from discord import app_commands

from ..utils.discord_helpers import reply_private


def flight_briefing(departure: str, arrival: str, aircraft: str, departure_utc: str, notes: str) -> discord.Embed:
    airports = [code.strip().upper() for code in (departure, arrival)]
    if any(not re.fullmatch(r"[A-Z]{4}", code) for code in airports):
        raise ValueError("Enter four-letter ICAO airport codes, such as EGLL and EHAM.")
    if departure_utc and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}", departure_utc):
        raise ValueError("Enter departure in UTC as YYYY-MM-DD HH:MM.")
    try:
        when = datetime.strptime(departure_utc, "%Y-%m-%d %H:%M").replace(tzinfo=UTC) if departure_utc else None
    except ValueError as error:
        raise ValueError("Enter a valid UTC departure date and time.") from error
    embed = discord.Embed(title=f"Flight briefing · {airports[0]} → {airports[1]}", color=0x3180D8)
    if aircraft:
        embed.add_field(name="Aircraft", value=discord.utils.escape_markdown(aircraft), inline=True)
    if when:
        embed.add_field(name="Departure", value=f"{discord.utils.format_dt(when, 'F')} · {when:%H:%M} UTC", inline=True)
    if notes:
        embed.add_field(name="Briefing notes", value=notes, inline=False)
    return embed.set_footer(text="Flight simulation only")


@app_commands.command(name="flightbrief", description="Build a flight-simulation briefing for a route.")
@app_commands.describe(departure="Four-letter departure airport code", arrival="Four-letter arrival airport code",
                       departure_utc="UTC date and time as YYYY-MM-DD HH:MM",
                       publish="Post in this channel with Manage Messages permission")
async def brief_flight(interaction: discord.Interaction, departure: str, arrival: str,
                       aircraft: app_commands.Range[str, 1, 80] | None = None,
                       departure_utc: app_commands.Range[str, 1, 16] | None = None,
                       notes: app_commands.Range[str, 1, 900] | None = None,
                       publish: bool = False) -> None:
    if publish and not interaction.permissions.manage_messages:
        await reply_private(interaction, "You need Manage Messages to publish a flight briefing.")
        return
    if not interaction.app_permissions.embed_links:
        await reply_private(interaction, "The bot needs Embed Links in this channel.")
        return
    try:
        embed = flight_briefing(departure, arrival, aircraft or "", (departure_utc or "").strip(), notes or "")
    except ValueError as error:
        await reply_private(interaction, str(error))
        return
    await interaction.response.send_message(embed=embed, ephemeral=not publish, allowed_mentions=discord.AllowedMentions.none())
