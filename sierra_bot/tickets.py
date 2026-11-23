import asyncio
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import discord

from .config import Settings
from .message_style import render_description
from .ticket_config import TicketRoute, TicketType

logger = logging.getLogger(__name__)


class TicketError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ticket:
    id: int
    owner_id: int
    channel_id: int | None
    subject: str
    opened_at: str
    closed_at: str | None
    route: TicketRoute | None = None

    @property
    def topic(self) -> str:
        return f"Sierra Steward ticket #{self.id} · member {self.owner_id}"


class Tickets:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path: Path = settings.database_path
        self.lock = asyncio.Lock()
        self.retained_staff_role_ids: set[int] = set()

    def query(self, sql: str, parameters: tuple = ()) -> list[tuple]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            return db.execute(sql, parameters).fetchall()

    async def initialize(self) -> None:
        self.retained_staff_role_ids = await asyncio.to_thread(self.migrate)

    def migrate(self) -> set[int]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                channel_id TEXT UNIQUE,
                subject TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT,
                type_name TEXT,
                route_category_id TEXT,
                route_role_id TEXT
            )""")
            existing = {column[1] for column in db.execute("PRAGMA table_info(tickets)")}
            for name in ("type_name", "route_category_id", "route_role_id"):
                if name not in existing:
                    db.execute(f"ALTER TABLE tickets ADD COLUMN {name} TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_ticket ON tickets(guild_id, owner_id) WHERE closed_at IS NULL")
            return {int(role_id) for role_id, in db.execute(
                "SELECT DISTINCT route_role_id FROM tickets WHERE guild_id = ? AND route_role_id IS NOT NULL",
                (str(self.settings.guild_id),),
            )}

    async def select(self, condition: str = "1 = 1", parameters: tuple = ()) -> list[Ticket]:
        rows = await asyncio.to_thread(
            self.query,
            "SELECT id, owner_id, channel_id, subject, opened_at, closed_at, type_name, route_category_id, route_role_id FROM tickets "
            f"WHERE guild_id = ? AND {condition} ORDER BY id DESC LIMIT 100",
            (str(self.settings.guild_id), *parameters),
        )
        return [
            Ticket(number, int(owner), int(channel) if channel else None, subject, opened, closed,
                   TicketRoute(name, int(category) if category else None, int(role) if role else None) if name else None)
            for number, owner, channel, subject, opened, closed, name, category, role in rows
        ]

    def ticket_type(self, key: str) -> TicketType | None:
        return next((kind for kind in self.settings.ticket_types if kind.key == key), None)

    def route(self, key: str | None) -> TicketRoute:
        types = self.settings.ticket_types
        if key is not None:
            kind = self.ticket_type(key)
            if kind is None:
                raise TicketError("That ticket type is no longer available. Choose a current type.")
        elif len(types) == 1:
            kind = types[0]
        elif types:
            raise TicketError("Choose a ticket type from the command or the support panel.")
        else:
            kind = None
        return TicketRoute(
            kind.name if kind else "Support ticket",
            (kind.category_id if kind else None) or self.settings.ticket_category_id,
            (kind.staff_role_id if kind else None) or self.settings.ticket_staff_role_id,
        )

    def member_can_close(self, member: discord.Member, ticket: Ticket) -> bool:
        staff_id = ticket.route.staff_role_id if ticket.route else self.settings.ticket_staff_role_id
        return (
            (member.id == ticket.owner_id and self.settings.ticket_allow_owner_close)
            or member.guild_permissions.manage_channels
            or any(role.id == staff_id for role in member.roles)
        )

    def destination(self, member: discord.Member, route: TicketRoute) -> tuple[discord.CategoryChannel | None, dict]:
        guild = member.guild
        bot = guild.me
        if bot is None or not bot.guild_permissions.manage_channels or not bot.guild_permissions.manage_roles:
            raise TicketError("The bot needs Manage Channels and Manage Roles to create private tickets.")
        access = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        overrides = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: access,
            bot: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, embed_links=True,
            ),
        }
        staff_id = route.staff_role_id
        if staff_id is not None:
            role = guild.get_role(staff_id)
            if role is None or role.is_default() or role.managed or role.id == self.settings.member_role_id:
                raise TicketError("Choose a dedicated staff role on this server for this ticket type.")
            overrides[role] = access
        category = guild.get_channel(route.category_id) if route.category_id else None
        if route.category_id and not isinstance(category, discord.CategoryChannel):
            raise TicketError("The Discord category for this ticket type could not be found.")
        return category, overrides

    async def open(self, member: discord.Member, subject: str, kind: str | None = None) -> tuple[discord.TextChannel, bool]:
        subject = subject.strip()
        guild = member.guild
        if guild.id != self.settings.guild_id or member.bot or member.pending:
            raise TicketError("Complete the server’s membership screening before opening a ticket.")
        if not 1 <= len(subject) <= 300:
            raise TicketError("Enter a ticket subject between 1 and 300 characters.")
        route = self.route(kind)
        async with self.lock:
            previous = await self.select("owner_id = ? AND closed_at IS NULL", (str(member.id),))
            if previous:
                ticket = previous[0]
                if ticket.channel_id is not None:
                    try:
                        channel = await guild.fetch_channel(ticket.channel_id)
                    except discord.NotFound:
                        await self.mark_closed(ticket, "Channel unavailable")
                    else:
                        if (isinstance(channel, discord.TextChannel) and channel.topic == ticket.topic
                                and channel.name != f"closed-{ticket.id}"):
                            return channel, False
                        await self.mark_closed(ticket, "Channel archived or no longer a ticket")
                else:
                    # Recover a channel if Discord created it before the last request returned.
                    for channel in await guild.fetch_channels():
                        if isinstance(channel, discord.TextChannel) and channel.topic == ticket.topic:
                            await self.attach(ticket, channel.id)
                            return channel, False
                    pending_route = ticket.route or TicketRoute("Support ticket", self.settings.ticket_category_id, self.settings.ticket_staff_role_id)
                    try:
                        category, overrides = self.destination(member, pending_route)
                    except TicketError:
                        # The old pending record has no channel and its route is no longer usable.
                        self.destination(member, route)
                        await self.mark_closed(ticket, "Pending ticket route was removed")
                    else:
                        return await self.create(member, ticket, pending_route, category, overrides), True

            category, overrides = self.destination(member, route)
            await asyncio.to_thread(
                self.query,
                "INSERT INTO tickets (guild_id, owner_id, subject, opened_at, type_name, route_category_id, route_role_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(guild.id), str(member.id), subject, discord.utils.utcnow().isoformat(timespec="seconds"), route.name,
                 str(route.category_id) if route.category_id else None, str(route.staff_role_id) if route.staff_role_id else None),
            )
            if route.staff_role_id is not None:
                self.retained_staff_role_ids.add(route.staff_role_id)
            ticket, = await self.select("owner_id = ? AND closed_at IS NULL", (str(member.id),))
            return await self.create(member, ticket, route, category, overrides), True

    async def create(
        self, member: discord.Member, ticket: Ticket, route: TicketRoute,
        category: discord.CategoryChannel | None, overrides: dict,
    ) -> discord.TextChannel:
        guild = member.guild
        channel = await guild.create_text_channel(
            f"{self.settings.ticket_channel_prefix}-{ticket.id}", topic=ticket.topic, category=category,
            overwrites=overrides,
            reason=f"Sierra Steward ticket #{ticket.id}",
        )
        await self.attach(ticket, channel.id)
        greeting = ""
        if self.settings.ticket_starter_message:
            greeting = render_description(
                self.settings.ticket_starter_message, member=member.mention, ticket=str(ticket.id),
                type=discord.utils.escape_markdown(route.name, ignore_links=False),
                server=discord.utils.escape_markdown(guild.name, ignore_links=False),
            )[:3500]
        subject = discord.utils.escape_markdown(ticket.subject, ignore_links=False)
        footer = "Use /ticket close to archive this conversation." if self.settings.ticket_allow_owner_close else "Ask staff to close this ticket when it is resolved."
        opening = discord.Embed(title=f"{route.name} #{ticket.id}", description=greeting or subject,
                                color=self.settings.ticket_panel_style.color).set_footer(text=footer)
        if greeting:
            opening.add_field(name="Subject", value=subject, inline=False)
        try:
            await channel.send(
                embed=opening,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Could not send the starter message for ticket #%s", ticket.id)
        return channel

    async def attach(self, ticket: Ticket, channel_id: int) -> None:
        await asyncio.to_thread(self.query, "UPDATE tickets SET channel_id = ? WHERE id = ?", (str(channel_id), ticket.id))

    async def mark_closed(self, ticket: Ticket, reason: str) -> None:
        await asyncio.to_thread(
            self.query, "UPDATE tickets SET closed_at = ?, close_reason = ? WHERE id = ? AND closed_at IS NULL",
            (discord.utils.utcnow().isoformat(timespec="seconds"), reason, ticket.id),
        )

    async def close(self, guild: discord.Guild, ticket: Ticket, reason: str) -> None:
        if guild.id != self.settings.guild_id or not ticket.channel_id:
            raise TicketError("This ticket has no Discord channel to archive.")
        reason = reason.strip()
        if reason and len(reason) > 300:
            raise TicketError("Use at most 300 characters for the close reason.")
        async with self.lock:
            current = await self.select("id = ?", (ticket.id,))
            if not current or current[0].closed_at:
                raise TicketError("This ticket is already closed.")
            try:
                channel = await guild.fetch_channel(ticket.channel_id)
            except discord.NotFound:
                await self.mark_closed(ticket, reason or "Channel unavailable")
                return
            if not isinstance(channel, discord.TextChannel) or channel.topic != ticket.topic:
                raise TicketError("The channel no longer matches this Sierra Steward ticket.")
            if channel.name != f"closed-{ticket.id}":
                if guild.me is None:
                    raise TicketError("The bot’s server permissions are unavailable.")
                overrides = channel.overwrites
                for target, permission in overrides.items():
                    if target.id != guild.me.id:
                        permission.update(
                            send_messages=False, send_messages_in_threads=False,
                            create_public_threads=False, create_private_threads=False, add_reactions=False,
                        )
                await channel.edit(
                    name=f"closed-{ticket.id}", overwrites=overrides,
                    reason=f"Sierra Steward closed ticket #{ticket.id}",
                )
            await self.mark_closed(ticket, reason or "Closed")
