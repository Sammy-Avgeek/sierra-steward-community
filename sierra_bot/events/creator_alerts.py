import asyncio
import hashlib
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing, suppress
from dataclasses import dataclass

import aiohttp
import discord

from ..utils.discord_helpers import can_send_embed
from .flight_feeds import NEWS_APPS, STEAM_APPS, STEAM_PRICES, SteamPrice, steam_news, steam_price

logger = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


@dataclass(frozen=True, slots=True)
class Alert:
    id: str
    title: str
    creator: str
    url: str
    label: str
    image_url: str = ""


def steam_artwork(app_id: int) -> str:
    return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg"


def youtube_entries(body: bytes) -> list[Alert]:
    if len(body) > 1_000_000 or b"<!DOCTYPE" in body.upper():
        raise ValueError("Unexpected YouTube feed format.")
    root = ET.fromstring(body)
    creator = root.findtext(f"{ATOM}author/{ATOM}name", "YouTube") or "YouTube"
    result = []
    for entry in root.findall(f"{ATOM}entry")[:15]:
        video = entry.findtext(f"{YT}videoId", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            result.append(Alert(video, entry.findtext(f"{ATOM}title", "New upload"), creator,
                                f"https://www.youtube.com/watch?v={video}", "New YouTube upload",
                                f"https://i.ytimg.com/vi/{video}/hqdefault.jpg"))
    return result


class CreatorAlerts:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.settings = bot.settings
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self) -> None:
        for name, enabled, interval, poll in (
            ("YouTube alerts", self.settings.youtube_channels, 180, self.poll),
            ("Flight sim news", self.settings.flight_news_channel_id, 900, self.poll_news),
            ("Steam offers", self.settings.special_offers_channel_id, 3600, self.poll_offers),
        ):
            task = self.tasks.get(name)
            if enabled and (task is None or task.done()):
                self.tasks[name] = asyncio.create_task(self.run(poll, interval), name=name)

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        for task in self.tasks.values():
            try:
                with suppress(asyncio.CancelledError):
                    await task
            except Exception:
                logger.exception("The %s task stopped unexpectedly.", task.get_name())
        self.tasks.clear()

    def database(self, sql: str, parameters: tuple = ()) -> list[tuple]:
        with closing(sqlite3.connect(self.settings.database_path, timeout=5)) as db, db:
            return db.execute(sql, parameters).fetchall()

    async def initialize(self) -> None:
        await asyncio.to_thread(
            self.database,
            """CREATE TABLE IF NOT EXISTS creator_alerts (
                guild_id TEXT NOT NULL, source TEXT NOT NULL, item TEXT NOT NULL,
                PRIMARY KEY (guild_id, source, item)
            )""",
        )
        await asyncio.to_thread(
            self.database,
            """CREATE TABLE IF NOT EXISTS steam_offers (
                guild_id TEXT NOT NULL, country TEXT NOT NULL, app_id INTEGER NOT NULL,
                offer TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY (guild_id, country, app_id)
            )""",
        )

    async def remember(self, source: str, item: str) -> None:
        await asyncio.to_thread(
            self.database, "INSERT OR IGNORE INTO creator_alerts (guild_id, source, item) VALUES (?, ?, ?)",
            (str(self.settings.guild_id), source, item),
        )

    async def report(self, channel: discord.TextChannel, source: str, alerts: list[Alert]) -> None:
        known = await asyncio.to_thread(
            self.database, "SELECT item FROM creator_alerts WHERE guild_id = ? AND source = ?",
            (str(self.settings.guild_id), source),
        )
        seen = {row[0] for row in known}
        if "!seed" not in seen:
            for alert in alerts:
                await self.remember(source, alert.id)
            await self.remember(source, "!seed")
            return
        for alert in reversed(alerts):
            if alert.id in seen:
                continue
            nonce = hashlib.sha256(f"{self.settings.guild_id}:{source}:{alert.id}".encode()).hexdigest()[:24]
            embed = discord.Embed(title=alert.title[:256], description=alert.creator[:1024], url=alert.url,
                                  color=discord.Color.blurple()).set_author(name=alert.label)
            if alert.image_url:
                embed.set_image(url=alert.image_url)
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), nonce=nonce)
            await self.remember(source, alert.id)
            seen.add(alert.id)

    def destination(self, channel_id: int, label: str) -> discord.TextChannel | None:
        guild = self.bot.get_guild(self.settings.guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel) or not can_send_embed(channel, guild.me):
            logger.warning("%s need a writable Discord text channel.", label)
            return None
        return channel

    async def poll(self, http: aiohttp.ClientSession) -> None:
        channel = self.destination(self.settings.alert_channel_id, "YouTube alerts")
        if channel is None:
            return
        for channel_id in self.settings.youtube_channels:
            try:
                async with http.get(
                    "https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id},
                    allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    body = await read_feed(response.content)
                await self.report(channel, f"youtube:{channel_id}", youtube_entries(body))
            except (aiohttp.ClientError, OSError, ValueError, ET.ParseError, discord.HTTPException):
                logger.warning("Could not complete the YouTube alert check for %s", channel_id)

    async def poll_news(self, http: aiohttp.ClientSession) -> None:
        channel = self.destination(self.settings.flight_news_channel_id, "Flight sim news")
        if channel is None:
            return
        for app_id in NEWS_APPS:
            try:
                async with http.get(f"https://steamcommunity.com/games/{app_id}/rss/", allow_redirects=False) as response:
                    response.raise_for_status()
                    items = steam_news(await read_feed(response.content), app_id)
                if items:
                    alerts = [Alert(key, title, STEAM_APPS[app_id], url, "Flight sim news", steam_artwork(app_id))
                              for key, title, url in items]
                    await self.report(channel, f"steam-news:{app_id}", alerts)
            except (aiohttp.ClientError, OSError, ValueError, ET.ParseError, discord.HTTPException):
                logger.warning("Could not check the publisher news feed for %s.", STEAM_APPS[app_id])

    async def report_offer(self, channel: discord.TextChannel, app_id: int, price: SteamPrice) -> None:
        key = (str(self.settings.guild_id), self.settings.steam_store_country, app_id)
        rows = await asyncio.to_thread(self.database, "SELECT offer, revision FROM steam_offers WHERE guild_id = ? AND country = ? AND app_id = ?", key)
        previous, revision = rows[0] if rows else (None, 0)
        if previous == price.offer_key:
            return
        revision += 1
        if price.discount:
            store = f"https://store.steampowered.com/app/{app_id}/"
            price_text = f"**{price.final_formatted}**"
            if price.initial_formatted:
                price_text += f" (was {price.initial_formatted})"
            nonce = hashlib.sha256(f"steam-offer:{key}:{revision}:{price.offer_key}".encode()).hexdigest()[:24]
            await channel.send(
                embed=discord.Embed(
                    title=f"{STEAM_APPS[app_id]} · {price.discount}% off", url=store,
                    description=price_text, color=discord.Color.gold(),
                ).set_author(name=f"Steam offer · {key[1].upper()}").set_image(url=steam_artwork(app_id)),
                allowed_mentions=discord.AllowedMentions.none(), nonce=nonce,
            )
        await asyncio.to_thread(self.database, """
            INSERT INTO steam_offers (guild_id, country, app_id, offer, revision) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, country, app_id) DO UPDATE SET offer = excluded.offer, revision = excluded.revision
        """, (*key, price.offer_key, revision))

    async def poll_offers(self, http: aiohttp.ClientSession) -> None:
        channel = self.destination(self.settings.special_offers_channel_id, "Steam offers")
        if channel is None:
            return
        for app_id in STEAM_APPS:
            try:
                async with http.get(STEAM_PRICES, params={
                    "appids": str(app_id), "filters": "price_overview", "cc": self.settings.steam_store_country,
                }, allow_redirects=False) as response:
                    response.raise_for_status()
                    price = steam_price(await read_feed(response.content), app_id)
                await self.report_offer(channel, app_id, price)
            except (aiohttp.ClientError, OSError, ValueError, discord.HTTPException):
                logger.warning("Could not check the Steam price for %s.", STEAM_APPS[app_id])

    async def run(self, poll, interval: int) -> None:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            while not self.bot.is_closed():
                if self.bot.is_ready():
                    try:
                        await poll(http)
                    except sqlite3.Error:
                        logger.exception("Could not read creator alert history.")
                await asyncio.sleep(interval)


async def read_feed(stream: aiohttp.StreamReader) -> bytes:
    data = bytearray()
    async for chunk in stream.iter_chunked(16_384):
        data.extend(chunk)
        if len(data) > 1_000_000:
            raise ValueError("The creator feed is too large.")
    return bytes(data)
