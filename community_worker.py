import asyncio
import base64
import binascii
import json
import logging
import os
import sqlite3
import sys
from threading import Thread

import aiohttp
import discord

from sierra_bot.bot import SierraBot
from sierra_bot.commands.tickets import find_ticket_panel, send_ticket_panel
from sierra_bot.config import PROJECT_DIR, SETTINGS_FIELDS, ConfigError, Settings, message_style, read_local_settings, save_local_settings
from sierra_bot.embed_json import MAX_JSON_SIZE, load_message_json, read_message
from sierra_bot.runtime import AlreadyRunningError, configure_logging, installation_lock
from sierra_bot.server_health import check_server
from sierra_bot.events.welcome import build_member_message
from sierra_bot.tickets import Tickets
from sierra_bot.utils.discord_helpers import can_send_embed


async def run_bot(settings: Settings, token: str) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()

    def wait_for_stop() -> None:
        # The dashboard holds the pipe open until Stop or Quit. A closed pipe also
        # stops the worker if Electron exits unexpectedly.
        try:
            os.read(sys.stdin.fileno(), 128)
        except OSError:
            pass
        if not task.done() and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)

    Thread(target=wait_for_stop, daemon=True, name="Community dashboard controls").start()
    async with SierraBot(settings) as bot:
        try:
            await bot.start(token)
        except asyncio.CancelledError:
            pass


def read_request() -> dict:
    raw = sys.stdin.buffer.read(MAX_JSON_SIZE + 1)
    if len(raw) > MAX_JSON_SIZE:
        raise ValueError("Keep requests under 2 MB.")
    request = json.loads(raw or b"{}")
    if not isinstance(request, dict):
        raise ValueError("The dashboard request is invalid.")
    return request


def saved_settings() -> dict:
    values = read_local_settings()
    return {"fields": {key: values.get(key) or "" for key in SETTINGS_FIELDS}, "hasToken": bool(values.get("DISCORD_TOKEN"))}


async def discord_action(action: str, request: dict, settings: Settings, token: str):
    if action == "check":
        return await check_server(settings, token)
    if action == "tickets":
        if not settings.database_path.is_file():
            return []
        try:
            tickets = await Tickets(settings).select()
        except sqlite3.Error as error:
            raise ValueError("Start the bot once to initialize the ticket database.") from error
        return [{"number": ticket.id, "channel": str(ticket.channel_id) if ticket.channel_id else "",
                 "open": ticket.closed_at is None, "guild": str(settings.guild_id),
                 "type": ticket.route.name if ticket.route is not None else "Support"} for ticket in tickets]
    async with asyncio.timeout(20), discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        if client.application_id != settings.client_id:
            raise ValueError("The Application ID and token must belong to the same Discord bot.")
        if action == "branding":
            name = request["name"].strip()
            if not 2 <= len(name) <= 32:
                raise ValueError("Enter a Discord bot name from 2 to 32 characters.")
            try:
                logo = base64.b64decode(request.get("logo") or "", validate=True)
            except binascii.Error as error:
                raise ValueError("Choose a PNG or JPEG logo.") from error
            if len(logo) > 1_250_000:
                raise ValueError("Choose a logo under 1.25 MB.")
            params = {"username": name}
            if logo:
                params["avatar"] = logo
            await client.user.edit(**params)
            if logo:
                try:
                    await client.application.edit(icon=logo)
                except discord.HTTPException:
                    return "Discord bot name and avatar updated. Discord declined the application icon update."
            return "Discord branding updated."
        guild = await client.fetch_guild(settings.guild_id)
        bot, channels = await asyncio.gather(guild.fetch_member(client.user.id), guild.fetch_channels())
        destinations = {channel.id: channel for channel in channels
                        if isinstance(channel, discord.TextChannel) and can_send_embed(channel, bot)}
        if action == "channels":
            return [{"id": str(channel.id), "name": channel.name} for channel in destinations.values()]
        if action in {"publish", "ticket-panel"}:
            channel = destinations.get(int(request["channel"]))
            if channel is None:
                raise ValueError("Choose a writable channel in the configured server.")
            if action == "ticket-panel":
                existing = None
                if request.get("update") is True:
                    if not channel.permissions_for(bot).read_message_history:
                        raise ValueError("Grant the bot Read Message History to find the existing panel.")
                    existing = await find_ticket_panel(channel, bot.id)
                    if existing is None:
                        raise ValueError("No Sierra Steward ticket panel was found in this channel's latest 100 messages.")
                panel = await send_ticket_panel(channel, settings, existing)
                return panel.jump_url
            draft = parse_embed(request)
            posted = await channel.send(content=draft.content, embeds=list(draft.embeds),
                                        allowed_mentions=discord.AllowedMentions.none())
            return posted.jump_url
    raise ValueError("Unknown Discord action.")


def parse_embed(request: dict):
    _, messages = load_message_json(request["json"])
    selected = request.get("index", 0)
    if type(selected) is not int or not 0 <= selected < len(messages):
        raise ValueError("Select a message in the embed file.")
    return read_message(messages[selected][1])


def command(action: str, request: dict):
    if action == "load":
        return saved_settings()
    if action == "save":
        fields, token = request["fields"], request.get("token", "")
        if not isinstance(fields, dict) or not isinstance(token, str) or not all(isinstance(value, str) for value in fields.values()):
            raise ValueError("Settings must contain text values.")
        save_local_settings(fields, token)
        return saved_settings()
    if action == "preview":
        _, messages = load_message_json(request["json"])
        return {"names": [name for name, _ in messages], "message": parse_embed(request).to_dict()}
    if action == "member-preview":
        event = request["event"]
        if event not in {"WELCOME", "GOODBYE"} or not isinstance(request["fields"], dict):
            raise ValueError("Choose a welcome or goodbye message.")
        style = message_style(event, request["fields"])
        embed = build_member_message(style, request.get("server") or "Your server", "Alex Pilot")
        return {"embed": embed.to_dict(), "banner": event.lower() if not style.image_url else ""}
    settings, token = Settings.from_env()
    return asyncio.run(discord_action(action, request, settings, token))


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    action = sys.argv[1]
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    if action == "start":
        configure_logging(PROJECT_DIR / "logs", [logging.StreamHandler()])
        try:
            with installation_lock(PROJECT_DIR):
                settings, token = Settings.from_env()
                asyncio.run(run_bot(settings, token))
        except (ConfigError, AlreadyRunningError) as error:
            logging.getLogger(__name__).error("%s", error)
        except discord.LoginFailure:
            logging.getLogger(__name__).error("Discord rejected the saved token.")
        except discord.PrivilegedIntentsRequired:
            logging.getLogger(__name__).error("Enable Server Members Intent in the Discord Developer Portal.")
        except discord.HTTPException as error:
            logging.getLogger(__name__).error("Discord startup failed (HTTP %s). Check the server ID and app installation.", error.status)
        else:
            return 0
        return 1

    try:
        output = {"value": command(action, read_request())}
    except (ConfigError, ValueError) as error:
        output = {"error": str(error)}
    except discord.LoginFailure:
        output = {"error": "Discord rejected the saved token."}
    except (discord.HTTPException, aiohttp.ClientError, OSError, TimeoutError, KeyError, TypeError):
        output = {"error": "Could not complete this action. Check the Discord connection, settings, and channel permissions."}
    print(json.dumps(output, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
