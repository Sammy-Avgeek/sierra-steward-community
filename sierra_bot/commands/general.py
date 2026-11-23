import math

import discord
from discord import app_commands


@app_commands.command(name="ping", description="Show the bot's Discord Gateway latency.")
async def ping(interaction: discord.Interaction) -> None:
    latency = interaction.client.latency
    if not math.isfinite(latency) or latency < 0:
        message = "Gateway latency is warming up. Try again in a moment."
    else:
        message = f"Pong! Gateway latency is **{latency * 1000:.0f} ms**."
    await interaction.response.send_message(message, ephemeral=True)
